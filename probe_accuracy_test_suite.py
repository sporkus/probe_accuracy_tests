#!/usr/bin/env python3

# klipper probe accuracy test suite
# Copyright (C) 2023 Foon Wong

# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

# ------------------------------------------------------------------------------
# Automating probe_accuracy testing
# The following three tests will be done:
# 0) 20 tests, 5 samples at bed center
#       - check consistency within normal measurements
# 1) 1 test, 100 samples at bed center - check for drift
# 2) 1 test, 30 samples at each bed mesh corners
#       - check if there are issues with individual z drives
# Notes:
# * First probe measurements are dropped
# ------------------------------------------------------------------------------

import argparse
import math
import os
import re
import subprocess
import sys
import requests
from datetime import datetime
from typing import Dict, List, Tuple
from matplotlib import pyplot
import numpy
from numpy.polynomial import Polynomial
import pandas

MOONRAKER_URL = "http://localhost:7125"
KLIPPY_LOG = "~/klipper_logs/klippy.log"
DATA_DIR = "/tmp"
RUNID = datetime.now().strftime("%Y%m%d_%H%M")
CLEAR_LINE = "\033[1A\x1b[2K"
# CLEAR_LINE = "\n\n"
class Probe():
    def __init__(self, printer):
        self.printer = printer

        self.isKlicky = False
        self.isKlippain = False
        self.isTap = False
        self.isBeacon = False

        self._detect()

    def is_present(self):
        return (
                self.isKlicky
            or  self.isKlippain
            or  self.isTap
            or  self.isBeacon
        )

    def lock(self, lock = True):
        if self.isKlicky:
            self.printer.gcode("ATTACH_PROBE_LOCK")
        if self.isKlippain:
            if lock:
                self.printer.gcode("ACTIVATE_PROBE LOCK=true")
            else:
                self.printer.gcode("ACTIVATE_PROBE")

    def unlock(self, unlock = False):
        if self.isKlicky:
            self.printer.gcode("DOCK_PROBE_UNLOCK")
        elif self.isKlippain:
            if unlock:
                self.printer.gcode("DEACTIVATE_PROBE UNLOCK=true")
            else:
                self.printer.gcode("DEACTIVATE_PROBE")

    def check_error(msg):
        klicky_macro_issue = " ".join([
            "!! Error evaluating 'gcode_macro PROBE_ACCURACY:gcode':",
            "CommandError:",
            "Must perform PROBE_ACCURACY with the probe above the BED!"
        ])
        if klicky_macro_issue == msgs:
            print("This issue can be fixed by updating klicky-macros.cfg")
            print(
                "Reference: https://github.com/jlas1/Klicky-Probe/commit/31a481c843567233c807bb310b6f0e83d60b4fca"
            )
            return

        print("Unknown probe error.")
        print("Exiting!")
        sys.exit(1)

    def _detect(self):
        print("Probe type: ..." )

        try:
            user_variables = self.printer.query("gcode_macro _User_Variables")
            if user_variables["docklocation_x"]:
                self.isKlicky = True
                print(f"{ CLEAR_LINE }Probe type: Klicky mode detected")
                return
        except:
            pass

        try:
            user_variables = self.printer.query("gcode_macro _USER_VARIABLES")
            if user_variables["probe_type_enabled"]:
                if user_variables["probe_type_enabled"] == "dockable":
                    self.isKlippain = True
                    print(f"{ CLEAR_LINE }Probe type: Klippain mode detected")
                    return
        except:
            pass

        try:
            backlash_comp = self.printer.config["idm"].get("backlash_comp", 0)
            #print(backlash_comp)
            if backlash_comp:
                self.isBeacon = True
                print(f"{ CLEAR_LINE }Probe type: IDM probe detected")
        except:
            try:
                backlash_comp = self.printer.config["beacon"].get("backlash_comp", 0)
                #print(backlash_comp)
                if backlash_comp:
                    self.isBeacon = True
                    print(f"{ CLEAR_LINE }Probe type: Beacon probe detected")
            except:
                try:
                    backlash_comp = self.printer.config["cartographer"].get("backlash_comp", 0)
                    #print(backlash_comp)
                    if backlash_comp:
                        self.isBeacon = True
                        print(f"{ CLEAR_LINE }Probe type: Cartographer probe detected")
                except:


                    try:
                        endstop_pin = self.printer.config["stepper_z"]["endstop_pin"]
                        #print(endstop_pin)

                        if re.search("probe:\s*z_virtual_endstop", endstop_pin):
                            self.isTap = True
                            print(f"{ CLEAR_LINE }Probe type: Tap mode detected")
                    except:
                        pass


class Printer:
    def __init__(self, moonraker_url):
        self.moonraker_url = moonraker_url
        self.config = self.query("configfile", "config")
        self.settings = self.query("configfile", "settings")

        self.probe = Probe(self)

        self.safe_z = None
        self.bed_center = self._get_bed_center()
        self.bed_corners = self._get_bed_corners()

    def get(self, endpoint, params = None):
        return requests.get(
            f"{ self.moonraker_url }{ endpoint }",
            params = params
        )

    def post(self, endpoint, params = None):
        return requests.post(
            f"{ self.moonraker_url }{ endpoint }",
            params = params
        )

    def query(self, object, key = None):
        """Query object"""
        response = self.get("/printer/objects/query", object).json()
        try:
            obj = response["result"]["status"][object]
            if key:
                obj = obj[key]
            return obj
        except:
            print(
                f"Warning: {object}.{key} is not configured",
                file = sys.stderr
                )
            return None

    def get_gcode_store(self, count = 1000):
        response = self.get("/server/gcode_store", { "count": count })
        return response.json()["result"]["gcode_store"]

    def gcode(self, gcode):
        """Send gcode to printer"""
        self.post("/printer/gcode/script", { "script": gcode })

    def conditional_home(self):
        """Home if not done already"""
        homed_axes = self.query("toolhead", "homed_axes")
        if homed_axes != "xyz":
            self._home()

    def move(
        self,
        x = None,
        y = None,
        z = None,
        feedrate = 99999,
        echo = False
    ):
        if not z:
            self._move_to_safe_z()
        self._move(x, y, z, feedrate, echo)

    def move_center(self):
        self.move(*self.bed_center)

    def move_random(self, max_range = 50):
        self.move(*self._get_random_loc(max_range))

    def level_bed(self, force = False):
        """Level bed if not done already or forced"""
        ztilt = self.config.get("z_tilt")
        qgl = self.config.get("quad_gantry_level")

        print("Leveling...")

        if ztilt:
            gcode_cmd = "z_tilt_adjust"
            leveled = self.query("z_tilt", "applied")
        elif qgl:
            gcode_cmd = "quad_gantry_level"
            leveled = self.query("quad_gantry_level", "applied")
        else:
            print(
                "User has no leveling gcode.",
                "Please check printer.cfg [z_tilt] or [quad_gantry_level]"
            )
            print(f"{CLEAR_LINE}Leveling... Skipped")
            return

        if (not leveled) or force:
            self.gcode(gcode_cmd)
            print(f"{CLEAR_LINE}Leveling... Done")

    def _print(self, msg):
        print(msg)
        self.gcode(f"M118 { msg }")

    def _home(self):
        print("Homing")
        self.gcode("G28")

    def _move(
        self,
        x = None,
        y = None,
        z = None,
        feedrate = None,
        echo = False
    ):
        gcode_cmd = "G0"
        if x != None:
            gcode_cmd += f" X{ x }"
        if y != None:
            gcode_cmd += f" Y{ y }"
        if z != None:
            gcode_cmd += f" Z{ z }"
        if feedrate != None:
            gcode_cmd += f" F{ feedrate }"

        if echo:
            self._print(gcode_cmd)

        self.gcode("G90")
        self.gcode(gcode_cmd)

    def _move_to_safe_z(self):
        if not self.safe_z:
            if self.probe.isKlicky:
                self.safe_z = self.query(
                    "gcode_macro _User_Variables",
                    "safe_z"
                )
            elif self.probe.isKlippain:
                self.safe_z = self.query(
                    "gcode_macro _USER_VARIABLES",
                    "probe_min_z_travel"
                )
            elif self.probe.isTap:
                self.safe_z = (
                    self.settings
                        .get("safe_z_home", {})
                        .get("z_hop", None)
                )
            elif self.probe.isBeacon:
                self.safe_z = 2

            if not self.safe_z:
                print(
                    "Safe z has not been set in klicky-variables",
                    "or in [safe_z_home]"
                )
                self.safe_z = input("Enter safe z height to avoid crash:")

        self._move(z = self.safe_z)

    def _get_bed_center(self) -> Tuple:
        xmin, ymin, _, _ = self.query("toolhead", "axis_minimum")
        xmax, ymax, _, _ = self.query("toolhead", "axis_maximum")

        x = numpy.mean([xmin, xmax])
        y = numpy.mean([ymin, ymax])
        return (x, y)

    def _get_random_loc(self, max_range = 50):
        xmin, ymin, _, _ = self.query("toolhead", "axis_minimum")
        xmax, ymax, _, _ = self.query("toolhead", "axis_maximum")

        x = (
              numpy.random.random()
            * (xmax - xmin - 2 * max_range)
            + max_range + xmin
        )
        y = (
              numpy.random.random()
            * (ymax - ymin - 2 * max_range)
            + max_range + ymin
        )
        return (x, y)


### TODO: if quad gantry level use those points to probe the corners, if not qgl, then calculate the corner probe points
    def _get_bed_corners(self) -> List:
        # try:
        #     corners_list = self.config["quad_gantry_level"]["points"]
        #     #print(f"{corners_list}")
        # except:
        #     pass
        try:
            x_offset = self.config["probe"].get("x_offset", 0)
            y_offset = self.config["probe"].get("y_offset", 0)
        except:
            try:
                x_offset = self.config["idm"].get("x_offset", 0)
                y_offset = self.config["idm"].get("y_offset", 0)
            except:
                try:
                    x_offset = self.config["cartographer"].get("x_offset", 0)
                    y_offset = self.config["cartographer"].get("y_offset", 0)
                except:
                    try:
                        x_offset = self.config["beacon"].get("x_offset", 0)
                        y_offset = self.config["beacon"].get("y_offset", 0)
                        except:
                            pass


        # print(f"x_offset{x_offset}\ny_offset{y_offset}")
        xmin, ymin = re.findall(r"[\d.]+", self.config["bed_mesh"]["mesh_min"])
        xmax, ymax = re.findall(r"[\d.]+", self.config["bed_mesh"]["mesh_max"])

        # print(f"xmin{xmin}\nymin{ymin}\nxmax{xmax}\nymax{ymax}")

        xmin = float(xmin) - float(x_offset)
        ymin = float(ymin) - float(y_offset)
        xmax = float(xmax) - float(x_offset)
        ymax = float(ymax) - float(y_offset)

        return [(xmin, ymax), (xmax, ymax), (xmin, ymin), (xmax, ymin)]


class Test_suite():
    def __init__(
        self,
        printer,
        corner,
        repeatability,
        drift,
        speedtest,
        force_dock = False,
        retract = False,
        speed = None,
        keep_first = False,
        output_dir = "/tmp",
        export_csv = False,
        **kwargs
    ):
        self.printer = printer

        self.corner = corner
        self.repeatability = repeatability
        self.drift = drift
        self.speedtest = speedtest

        self.force_dock = force_dock
        self.retract = retract
        self.speed = speed
        self.keep_first = keep_first
        self.output_dir = output_dir
        self.export_csv = export_csv

        self.testframes = []

    def run(self):
        if self.corner:
            self.test_corner()
        if self.repeatability:
            self.test_repeatability()
        if self.drift:
            self.test_drift()
        if self.speedtest:
            self.test_speedtest()

        suiteframe = pandas.concat(
            self.testframes,
            axis = 0,
            ignore_index = True
        ).sort_index()
        summary = self._summarize_results(suiteframe, echo = False)

        file_nm = f"{ RUNID }_probe_accuracy_test"
        if self.export_csv:
            suiteframe.to_csv(
                f"{ self.output_dir }/{ file_nm }.csv",
                index = False
            )
            summary.to_csv(
                f"{ self.output_dir }/{ file_nm }_summary.csv"
            )


    def test_corner(self):
        print("\nCorner test:")
        print(
            "Test probe around the bed to see if there are issues",
            "with individual drives"
        )

        if self.corner < 10:
            print(f"The minimum corner count is 10, updating test count from {self.corner} to 10")
            self.corner = 10

        self.printer.level_bed(force = True)
        if not self.force_dock:
            self.printer.probe.lock(lock = True)

        print(f"Test number: ", end = "", flush = True)
        dataframes = []
        for i, xy in enumerate(self.printer.bed_corners):
            print(f"{ 4 - i }...", end = "", flush = True)
            self.printer.gcode(f"M117 Corner test { i + 1 }/4")

            xy_txt = f"({xy[0]:.0f}, {xy[1]:.0f})"
            dataframe = self._test_probe(
                probe_count = self.corner,
                loc = xy,
                testname = f"{ i + 1}: corner { self.corner } samples {xy_txt}"
            )
            dataframe["measurement"] = f"{ i + 1 }: { xy_txt }"
            dataframes.append(dataframe)
        print("Done")

        if not self.force_dock:
            self.printer.probe.unlock(unlock = True)

        testframe = pandas.concat(dataframes, axis = 0)
        self._summarize_results(testframe)

        plot_nm = f"{ RUNID } Corner Test\n({ self.corner } samples)"
        self._facet_plot(testframe, cols = 2, plot_nm = plot_nm)
        self._plot_boxplot(testframe, plot_nm)

        self.testframes.append(testframe)


    def test_repeatability(self, probe_count = 10):
        print("\nRepeatability test:")
        print(
            f"Take { self.repeatability } probe_accuracy tests",
            "to check for repeatability"
        )

        if not self.force_dock:
            self.printer.probe.lock(lock = True)

        print("Test number: ", end="", flush=True)
        dataframes = []

        for i in range(self.repeatability):
            print(f"{self.repeatability - i}...", end="", flush=True)
            self.printer.gcode(
                f"M117 {i+1}/{ self.repeatability } repeatability"
            )

            for i in range(4):
                self.printer.move_random()
                dataframe = self._test_probe(
                    probe_count = probe_count,
                    testname=f"{i+1:02d}: center {probe_count} samples"
                )
                dataframe["measurement"] = f"Test #{i+1:02d}"
                dataframes.append(dataframe)
        print("Done")

        if not self.force_dock:
            self.printer.probe.unlock(unlock = True)

        testframe = pandas.concat(dataframes, axis = 0).sort_index()
        self._summarize_results(testframe)
        self._summarize_repeatability(testframe)

        plot_nm = f"{ RUNID } Repeatability Test\n({ probe_count } samples)"
        self._facet_plot(testframe, plot_nm = plot_nm)
        self._plot_boxplot(testframe, plot_nm)

        self.testframes.append(testframe)


    def test_drift(self):
        print("\nDrift test:")
        print(f"Take { self.drift } samples in a row to check for drift")

        testframe = self._test_probe(
            probe_count = self.drift,
            testname = f"center { self.drift } samples"
        )

        testframe["measurement"] = ""
        self._summarize_results(testframe)

        plot_nm = f"{ RUNID } Drift Test\n({ self.drift } samples)"
        fig, ax = pyplot.subplots()
        self._plot_probes(
            testframe["sample_index"].astype(int),
            testframe["z"],
            "",
            ax
        )
        fig.suptitle(plot_nm)
        fig.tight_layout()
        file_nm = plot_nm.split("\n")[0].lower().replace(" ", "_")
        fig.savefig(f"{ self.output_dir }/{ file_nm }.png")

        self.testframes.append(testframe)


    def test_speedtest(self):
        print("\nZ-Probe speed test:")
        print("Test a range of z-probe speed")

        try:
            print("")
            speedrange = {
                "start": float(input("Minimum speed?  ")),
                "stop": float(input("Maximum speed?  ")),
                "step": float(input("Steps between speeds?  ")),
            }
            print("")
            self._speedcheck(speedrange)
            speeds = list(numpy.arange(**speedrange))
            speeds.append(speedrange["stop"])
        except Exception as e:
            print("Invalid user input. Exiting...")
            print(e)
            sys.exit(0)

        self.printer.level_bed()
        if not self.force_dock:
            self.printer.probe.lock(lock = True)

        print("Test speeds: ", end="", flush=True)
        dataframes = []
        for spd in speeds:
            print(f"{ spd } mm/s...", end="", flush=True)
            self.printer.gcode(f"M117 { spd } mm/s probe speed")

            dataframe = self._test_probe(
                probe_count = 10,
                testname = spd,
                speed = spd
            )
            dataframe["measurement"] = f"Speed {spd: 2.1f}"
            dataframes.append(dataframe)
        print("Done")

        if not self.force_dock:
            self.printer.probe.unlock(unlock = True)

        testframe = pandas.concat(dataframes, axis = 0)
        self._summarize_results(testframe)

        plot_nm = f"{ RUNID } Speed Test)"
        self._facet_plot(testframe, cols = 5, plot_nm = plot_nm)
        self._plot_boxplot(testframe, plot_nm)

        self.testframes.append(testframe)


    def _speedcheck(self, speeds):
        assert speeds["step"] > 0
        assert speeds["start"] >= 1
        assert speeds["stop"] >= speeds["start"]

        if speeds["stop"] >= 35:
            print(f"Warning: your maxmimum speeds will be { speeds['stop'] }")
            confirm = None
            while not (confirm == "y" or confirm == "n"):
                confirm = input("confirm? (y/n) ")

            if confirm == "n":
                assert False


    def _test_probe(
        self,
        probe_count,
        loc = None,
        testname = "",
        keep_first = False,
        speed = None
    ):
        """
            Send probe_accuracy command, and retrieve data
            from gcod respond cache
        """
        if loc:
            self.printer.move(*loc)
        else:
            self.printer.move_center()

        start_time = self.printer.get_gcode_store(count = 1)[0]["time"]

        gcode_cmd = f"PROBE_ACCURACY SAMPLES={ probe_count }"
        if self.retract:
            gcode_cmd += f" SAMPLE_RETRACT_DIST={ self.retract }"
        if speed:
            gcode_cmd += f" PROBE_SPEED={ speed }"
        elif self.speed:
            gcode_cmd += f" PROBE_SPEED={ self.speed }"
        self.printer.gcode(gcode_cmd)

        raw = self.printer.get_gcode_store(count = 1000)
        gcode_resp = [
            x
                for x in raw
                    if x["time"] > start_time
        ]

        err_msgs = [
            x["message"]
                for x in gcode_resp
                    if x["message"].startswith("!!")
        ]
        msgs = [
            x["message"]
                for x in gcode_resp
                    if x["message"].startswith("// probe at")
        ]

        if len(err_msgs):
            print("\nSomething's wrong with probe_accuracy! Klipper response:")
            for msg in err_msgs:
                print(msg)
                self.printer.probe.check_error(msg)

        data = []
        for i, msg in enumerate(msgs):
            coor = re.findall(r"[\d.]+", msg)
            # print(f"\n\n {coor}")
            if self.printer.probe.isBeacon:
                x, y, _, z = [float(k) for k in coor]
            else:
                x, y, z = [float(k) for k in coor]
            data.append({
                "test": testname,
                "sample_index": i,
                "x": x,
                "y": y,
                "z": z
            })

        if len(data) == 0:
            print("\nNo measurements collected")
            print("Exiting!")
            sys.exit(1)

        try:
            printer_config_drop_first = self.printer.config["probe"].get("drop_first_result")
        except:
            printer_config_drop_first = False

        if (
                printer_config_drop_first # self.printer.config["probe"].get("drop_first_result") == "True"
            and not keep_first
        ):
            data.pop(0)

        return pandas.DataFrame(data)


    def _summarize_results(self, testframe, echo = True):
        summary = testframe.groupby("test")["z"].agg(
            ["min", "max", "first", "last", "mean", "std", "count"]
        )
        summary["range"] = summary["max"] - summary["min"]
        summary["drift"] = summary["last"] - summary["first"]

        if echo:
            print("")
            print(summary)

        return summary


    def _summarize_repeatability(self, testframe):
        try:
            probe_config = self.printer.config["probe"]
            agg_method = probe_config.get("samples_result")
            agg_method = "mean" if agg_method != "median" else "median"
        except:
            agg_method = "mean"

        n = testframe["sample_index"].drop_duplicates().shape[0]
        n_test = testframe["measurement"].drop_duplicates().shape[0]
        # If first sample was dropped, need to shift starting index to 1
        first_sample_dropped = (
            1 if (testframe["sample_index"].min() == 1) else 0
        )

        tmp = []
        for i in range(n):
            stats = (
                testframe[
                    testframe["sample_index"] <= (i + first_sample_dropped)
                ]
                    .groupby(["measurement"])
                    .z.agg(agg_method)
                    .agg(["mean", "min", "max", "std"])
                    .to_dict()
            )

            stats.update({
                "range": stats["max"] - stats["min"],
                "sample_count": i + 1
            })
            tmp.append(stats)

        msg = " ".join([
            "\nYour probe config uses",
            f'{ agg_method } of { testframe["sample_index"].max() + 1 }',
            f"sample(s) over { n_test } tests"
        ])
        if first_sample_dropped:
            msg += " with the first sample dropped"
        msg += ", ".join([
            f"\nBelow is the statistics on your { agg_method } Z values",
            "using different probe samples\n"
        ])

        print(msg)
        print(pandas.DataFrame(tmp))


    def _facet_plot(
        self,
        testframe,
        cols = 5,
        plot_nm = None,
    ):
        tf_group = testframe.groupby("measurement")
        rows = math.ceil(tf_group.ngroups / cols)
        fig, axs = pyplot.subplots(
            rows,
            cols,
            sharex = True,
            figsize = (cols * 6, rows * 5 + 3)
        )

        for (measurement, testframe), ax in zip(tf_group, axs.ravel()):
            x, y = testframe["sample_index"].astype(int), testframe["z"]
            self._plot_probes(x, y, measurement, ax)

        fig.suptitle(plot_nm)
        fig.tight_layout()
        file_nm = plot_nm.split("\n")[0].lower().replace(" ", "_")
        fig.savefig(f"{ self.output_dir }/{ file_nm }.png")


    def _plot_probes(self, x, y, measurement, ax):
        polynom = Polynomial.fit(x, y, deg=3)
        median = y.median()
        range = y.max() - y.min()
        range50 = y.quantile(0.75) - y.quantile(0.25)
        range_flag = "!" * math.floor(range / 0.01)
        std_flag = "!" if y.std() > 0.004 else ""
        ylim = round(median, 3) - 0.01, round(median, 3) + 0.01

        title = f"""
            {measurement}
            Mean:{y.mean():.4f}  Std:{y.std():.4f}{std_flag}
            Median:{y.median():.4f}  Mid 50% range:{range50:.4f}
            Range:{range:.4f}{range_flag}  Min:{y.min():.4f}  Max:{y.max():.4f}
        """

        outofbound = sum(y < median - 0.01) + sum(y > median + 0.01)
        if outofbound:
            title += " ".join([
                f"\n{outofbound} sample{'s are' if outofbound > 1 else ' is'}",
                "outside of median±0.01mm range"
            ])

        ax.plot(x, y, ".", x, polynom(x), "-.")
        ax.set(xlabel = "probe sample", ylabel = "z")
        ax.set_ylim(*ylim)
        ax.set_yticks(numpy.arange(ylim[0], ylim[1] + 0.002, 0.002))
        ax.fill_between(
            x,
            y.quantile(0.75),
            y.quantile(0.25),
            color = (0, 1, 0, 0.3)
        )
        ax.fill_between(x, median - 0.005, color = (1, 0, 0, 0.1))
        ax.fill_between(x, 100, median + 0.005, color = (1, 0, 0, 0.1))
        ax.set_title(title, fontsize=9)


    def _plot_boxplot(self, testframe, plot_nm = ""):
        pyplot.title(plot_nm)
        pyplot.suptitle("")

        file_nm = plot_nm.split("\n")[0].lower().replace(" ", "_")

        ax = testframe.boxplot(column="z", by="measurement", rot=45, fontsize=8)
        ax.figure.savefig(f"{ self.output_dir }/{ file_nm }(box).png")


def main(userparams):
    if not os.path.exists(userparams['output_dir']):
        os.makedirs(userparams['output_dir'], exist_ok=True)

    printer = Printer(MOONRAKER_URL)

    if not printer.probe.is_present():
        print("ERROR: No probe could be found.", file = sys.stderr)
        sys.exit(1)
    elif not userparams["detect_probe"]:
        try:
            printer.conditional_home()
            printer.move_center()

            if (not (
                    userparams["corner"]
                or  userparams["repeatability"]
                or  userparams["drift"]
                or  userparams["speedtest"]
            )):
                print("Running all tests")
                userparams.update({
                    "corner": 30,
                    "repeatability": 20,
                    "drift": 100
                })

            printer_test_suite = Test_suite(printer, **userparams)
            printer_test_suite.run()
        except KeyboardInterrupt:
            pass
        finally:
            printer.probe.unlock()
            printer.move_center()


def fetch_repo():
    script_path = os.path.realpath(__file__)
    repo_path = os.path.dirname(script_path)
    wd = os.getcwd()
    print(f"Changing directory to {repo_path}")
    os.chdir(repo_path)
    output = subprocess.run(["git", "pull"], capture_output=True)
    print(output.stdout.decode("utf-8"))
    print(f"Changing directory to {wd}. Please re-run without the --update flag")
    os.chdir(wd)
    pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description = """
            Automated probe testing.
            All three tests will run at default values unless individual tests are specified
        """
    )
    ap.add_argument(
        "-c",
        "--corner",
        nargs = "?",
        type = int,
        const = 30,
        help = "Enable corner test. Number of probe samples at each corner can be optionally provided. Default 30.",
    )
    ap.add_argument(
        "-r",
        "--repeatability",
        nargs = "?",
        type = int,
        const = 20,
        help = "Enable repeatability test. Number of probe_accuracy tests can be optionally provided. Default 20.",
    )
    ap.add_argument(
        "-d",
        "--drift",
        nargs = "?",
        type = int,
        const = 100,
        help = "Enable drift test. Number of probe_accuracy samples can be optionally provided. Default 100.",
    )
    ap.add_argument(
        "--speedtest",
        action = "store_true",
        help = "Enable probe speed test. Requires user input for speed parameters.",
    )
    ap.add_argument(
        "--export_csv",
        action = "store_true",
        help = "export data as csv",
    )
    ap.add_argument(
        "--force_dock",
        action = "store_true",
        help = "Force docking between tests. Default False",
    )
    ap.add_argument(
        "--keep_first",
        action = "store_true",
        help = "Keep first probe measurement",
    )
    ap.add_argument(
        "-s",
        "--speed",
        nargs = 1,
        type = float,
        help = "probe speed",
    )
    ap.add_argument(
        "--retract",
        nargs = 1,
        type = float,
        help = "probe sample retract distance",
    )
    ap.add_argument(
        "-o",
        "--output_dir",
        type = str,
        help = "Output folder for testresults",
    )
    ap.add_argument(
        "-u",
        "--update",
        action = "store_true",
        help = "Updates the script with git",
    )
    ap.add_argument(
        "-t",
        "--detect_probe",
        action = "store_true",
        help = "Simple test probe mode",
    )
    args = vars(ap.parse_args())

    if args["update"]:
        fetch_repo()
        sys.exit(0)

    if not args["output_dir"]:
        args["output_dir"] = DATA_DIR
    else:
        args["output_dir"] = str(args["output_dir"]).rstrip("/")

    main(args)
