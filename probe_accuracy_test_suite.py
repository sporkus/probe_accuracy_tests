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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.


# -----------------------------------------------------
# Automating probe_accuracy testing
# The following three tests will be done:
# 0) 20 tests, 5 samples at bed center - check consistency within normal measurements
# 1) 1 test, 100 samples at bed center - check for drift
# 2) 1 test, 30 samples at each bed mesh corners - check if there are issues with individual z drives
# Notes:
# * First probe measurements are dropped
# -----------------------------------------------------

import argparse
import math
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from numpy.polynomial import Polynomial
from requests import get, post

MOONRAKER_URL = "http://localhost:7125"
KLIPPY_LOG = f"{os.environ.get('HOME')}/klipper_logs/klippy.log"
DATA_DIR = f"{os.environ.get('HOME')}/probe_accuracy_tests/output"
RUNID = datetime.now().strftime("%Y%m%d_%H%M")
CFG = {}
TOOLHEAD = {}
isKlicky = False
isTap = False
safe_z = None


def main(userparams):
    if not os.path.exists(DATA_DIR):
        os.mkdir(DATA_DIR)
    try:
        CFG.update(query_printer_objects("configfile", "config"))
        TOOLHEAD.update(query_printer_objects("toolhead"))
        detect_probe()
        homing()
        level_bed()
        move_to_safe_z()

        if not any(
            [
                userparams["corner"]
                or userparams["repeatability"]
                or userparams["drift"]
                or userparams["speedtest"]
            ]
        ):
            print("Running all tests")
            userparams.update({"corner": 30, "repeatability": 20, "drift": 100})

        test_routine(**userparams)
    except KeyboardInterrupt:
        pass
    if isKlicky:
        send_gcode("DOCK_PROBE_UNLOCK")
    move_to_loc(*get_bed_center())


def test_routine(corner, repeatability, drift, export_csv, force_dock, **kwargs):
    dfs = []
    if corner:
        dfs.append(test_corners(n=corner, force_dock=force_dock, **kwargs))
    if repeatability:
        dfs.append(
            test_repeatability(
                test_count=repeatability,
                probe_count=10,
                force_dock=force_dock,
                **kwargs,
            )
        )
    if drift:
        dfs.append(test_drift(n=drift, **kwargs))
    if kwargs["speedtest"]:
        dfs.append(test_speed())
    df = pd.concat(dfs, axis=0, ignore_index=True).sort_index()
    summary = summarize_results(df, echo=False)

    file_nm = f"{RUNID}_probe_accuracy_test"

    if export_csv:
        df.to_csv(DATA_DIR + "/" + file_nm + ".csv", index=False)
        summary.to_csv(f"{DATA_DIR}/{file_nm}_summary.csv")


def test_drift(n=100, **kwargs):
    print(f"\nTake {n} samples in a row to check for drift")
    df = test_probe(probe_count=n, testname=f"center {n}samples", **kwargs)
    df["measurement"] = ""
    summary = summarize_results(df)
    plot_nm = f"{RUNID} Drift Test\n({n} samples)"
    fig, ax = plt.subplots()
    plot_probes(df["sample_index"].astype(int), df["z"], "", ax)
    fig.suptitle(plot_nm)
    fig.tight_layout()
    file_nm = plot_nm.split("\n")[0].lower().replace(" ", "_")
    fig.savefig(f"{DATA_DIR}/{file_nm}.png")

    return df


def test_repeatability(
    test_count=10, probe_count=6, force_dock=False, **kwargs
) -> pd.DataFrame:
    if isKlicky and not force_dock:
        send_gcode("ATTACH_PROBE_LOCK")

    print(f"\nTake {test_count} probe_accuracy tests to check for repeatability")
    dfs = []
    print("Test number: ", end="", flush=True)
    for i in range(test_count):
        for xy in get_random_loc(n=4):
            move_to_loc(*xy)
        move_to_loc(*get_bed_center())
        send_gcode(f"M117 {i+1}/{test_count} repeatability")
        print(f"{test_count - i}...", end="", flush=True)
        df = test_probe(
            probe_count, testname=f"{i+1:02d}: center {probe_count}samples", **kwargs
        )
        df["measurement"] = f"Test #{i+1:02d}"
        dfs.append(df)
    print("Done")
    if isKlicky and not force_dock:
        send_gcode("DOCK_PROBE_UNLOCK")

    df = pd.concat(dfs, axis=0).sort_index()
    summary = summarize_results(df)
    summarize_repeatability(df)
    plot_nm = f"{RUNID} Repeatability Test\n({probe_count} samples)"
    facet_plot(df, plot_nm=plot_nm)
    plot_boxplot(df, plot_nm)
    print("-" * 80)
    return df


def test_corners(n=30, force_dock=False, **kwargs):
    print(
        "\nTest probe around the bed to see if there are issues with individual drives"
    )
    level_bed(force=True)
    if isKlicky and not force_dock:
        send_gcode("ATTACH_PROBE_LOCK")
    dfs = []
    for i, xy in enumerate(get_bed_corners()):
        xy_txt = f"({xy[0]:.0f}, {xy[1]:.0f})"
        send_gcode(f"M117 corner test {i+1}/4")
        print(f"{4-i}...", end="", flush=True)
        df = test_probe(
            probe_count=n,
            loc=xy,
            testname=f"{i+1}:corner {n}samples {xy_txt}",
            **kwargs,
        )
        df["measurement"] = f"{i+1}: {xy_txt}"
        dfs.append(df)
    print("Done")
    if isKlicky and not force_dock:
        send_gcode("DOCK_PROBE_UNLOCK")
    df = pd.concat(dfs, axis=0)
    summary = summarize_results(df)
    plot_nm = f"{RUNID} Corner Test\n({n} samples)"
    facet_plot(df, cols=2, plot_nm=plot_nm)
    plot_boxplot(df, plot_nm)
    print("-" * 80)
    return df


def test_speed(force_dock=False, **kwargs):
    print("\nTest a range of z-probe speed")
    try:
        speedrange = {
            "start": float(input("\nMinimum speed?  ")),
            "stop": float(input("Maximum speed?  ")),
            "step": float(input("Steps between speeds?  ")),
        }
        speedcheck(speedrange)
        speeds = list(np.arange(**speedrange))
        speeds.append(speedrange["stop"])
    except Exception as e:
        print("Invalid user input. Exiting...")
        print(e)
        sys.exit(0)

    level_bed()
    if isKlicky and not force_dock:
        send_gcode("ATTACH_PROBE_LOCK")
    dfs = []
    for spd in speeds:
        send_gcode(f"M117 {spd}mm/s probe speed")
        print(f"{spd}mm/s...", end="", flush=True)
        df = test_probe(probe_count=10, testname=spd, speed=spd)
        df["measurement"] = f"Speed {spd: 2.1f}"
        dfs.append(df)
    print("Done")

    if isKlicky and not force_dock:
        send_gcode("DOCK_PROBE_UNLOCK")
    df = pd.concat(dfs, axis=0)
    summary = summarize_results(df)
    plot_nm = f"{RUNID} Speed Test)"
    facet_plot(df, cols=5, plot_nm=plot_nm)
    plot_boxplot(df, plot_nm)
    print("-" * 80)
    return df


def speedcheck(speeds):
    assert speeds["step"] > 0
    assert speeds["start"] >= 1
    assert speeds["stop"] >= speeds["start"]

    if speeds["stop"] >= 35:
        print(f"Warning: your maxmimum speeds will be {speeds['stop']}")
        confirm = None
        while not (confirm == "y" or confirm == "n"):
            confirm = input("confirm? (y/n) ")

        if confirm == "n":
            assert False


def summarize_results(df, echo=True):
    df_sum = df.groupby("test")["z"].agg(
        ["min", "max", "first", "last", "mean", "std", "count"]
    )
    df_sum["range"] = df_sum["max"] - df_sum["min"]
    df_sum["drift"] = df_sum["last"] - df_sum["first"]

    if echo:
        print(df_sum)
    return df_sum


def facet_plot(
    df,
    cols=5,
    plot_nm=None,
):
    dfg = df.groupby("measurement")
    rows = math.ceil(dfg.ngroups / cols)
    fig, axs = plt.subplots(rows, cols, sharex=True, figsize=(cols * 6, rows * 5 + 3))

    for (measurement, df), ax in zip(dfg, axs.ravel()):
        x, y = df["sample_index"].astype(int), df["z"]
        plot_probes(x, y, measurement, ax)

    fig.suptitle(plot_nm)
    fig.tight_layout()
    file_nm = plot_nm.split("\n")[0].lower().replace(" ", "_")
    fig.savefig(f"{DATA_DIR}/{file_nm}.png")


def plot_probes(x, y, measurement, ax):
    p = Polynomial.fit(x, y, deg=3)
    ax.plot(x, y, ".", x, p(x), "-.")
    median = y.median()
    range = y.max() - y.min()
    range50 = y.quantile(0.75) - y.quantile(0.25)
    range_flag = "!" * math.floor(range / 0.01)
    std_flag = "!" if y.std() > 0.004 else ""
    ylim = round(median, 3) - 0.01, round(median, 3) + 0.01
    outofbound = sum(y < median - 0.01) + sum(y > median + 0.01)
    ax.set(xlabel="probe sample", ylabel="z")
    ax.set_ylim(*ylim)
    ax.set_yticks(np.arange(ylim[0], ylim[1] + 0.002, 0.002))
    ax.fill_between(x, y.quantile(0.75), y.quantile(0.25), color=(0, 1, 0, 0.3))
    ax.fill_between(x, median - 0.005, color=(1, 0, 0, 0.1))
    ax.fill_between(x, 100, median + 0.005, color=(1, 0, 0, 0.1))
    title = f"""{measurement}
    Mean:{y.mean():.4f}  Std:{y.std():.4f}{std_flag}
    Median:{y.median():.4f}  Mid 50% range:{range50:.4f}
    Range:{range:.4f}{range_flag}  Min:{y.min():.4f}  Max:{y.max():.4f}"""
    if outofbound:
        title += f"\n{outofbound} sample{'s are' if outofbound > 1 else ' is'} outside of median±0.01mm range"
    ax.set_title(title, fontsize=9)


def plot_boxplot(df, plot_nm=""):
    ax = df.boxplot(column="z", by="measurement", rot=45, fontsize=8)
    plt.title(plot_nm)
    plt.suptitle("")
    file_nm = plot_nm.split("\n")[0].lower().replace(" ", "_")
    ax.figure.savefig(DATA_DIR + "/" + file_nm + "(box).png")
    pass


def summarize_repeatability(df):
    probe_config = CFG["probe"]
    agg_method = probe_config.get("samples_result")
    agg_method = "mean" if agg_method != "median" else "median"
    n = df["sample_index"].drop_duplicates().shape[0]
    n_test = df["measurement"].drop_duplicates().shape[0]
    # If first sample was dropped, need to shift starting index to 1
    first_sample_dropped = 1 if (df["sample_index"].min() == 1) else 0
    tmp = []
    for i in range(n):
        stats = (
            df[df["sample_index"] <= (i + first_sample_dropped)]
            .groupby(["measurement"])
            .z.agg(agg_method)
            .agg(["mean", "min", "max", "std"])
            .to_dict()
        )

        stats.update({"range": stats["max"] - stats["min"], "sample_count": i + 1})
        tmp.append(stats)

    msg = f"\nYour probe config uses {agg_method} of {probe_config['samples']} sample(s) over {n_test} tests"
    if first_sample_dropped:
        msg += " with the first sample dropped"
    msg += f"\nBelow is the statistics on your {agg_method} Z values, using different probe samples"
    print(msg)
    print(pd.DataFrame(tmp))


def send_gcode(gcode):
    gcode = re.sub(" ", "%20", gcode)
    url = f"{MOONRAKER_URL}/printer/gcode/script?script={gcode}"
    post(url)


def homing() -> None:
    """Home if not done already"""
    axes = query_printer_objects("toolhead", "homed_axes")
    if axes != "xyz":
        print("Homing")
        send_gcode("G28")


def level_bed(force=False) -> None:
    """Level bed if not done already"""
    ztilt = CFG.get("z_tilt")
    qgl = CFG.get("quad_gantry_level")

    if ztilt:
        gcode = "z_tilt_adjust"
        leveled = query_printer_objects("z_tilt", "applied")
    elif qgl:
        gcode = "quad_gantry_level"
        leveled = query_printer_objects("quad_gantry_level", "applied")
    else:
        print(
            "User has no leveling gcode. Please check printer.cfg [z_tilt] or [quad_gantry_level]"
        )
        print("Skip leveling...")
        return

    if (not leveled) or force:
        print("Leveling")
        send_gcode(gcode)


def move_to_safe_z():
    global safe_z

    if isKlicky:
        safe_z = query_printer_objects("gcode_macro _User_Variables", "safe_z")
    elif isTap:
        settings = query_printer_objects("configfile", "settings")
        if "safe_z_home" in settings:
            safe_z = settings["safe_z_home"]["z_hop"]

    if not safe_z:
        print("Safe z has not been set in klicky-variables or in [safe_z_home]")
        safe_z = input("Enter safe z height to avoid crash:")

    
    send_gcode(f"G1 Z{safe_z}")


def query_printer_objects(object, key=None):
    url = f"{MOONRAKER_URL}/printer/objects/query?{object}"
    resp = get(url).json()
    try:
        obj = resp["result"]["status"][object]
        if key:
            obj = obj[key]
        return obj
    except:
        print(f"Warning: {object}.{key} is not configured")
        return None


def get_bed_center() -> Tuple:
    xmin, ymin, _, _ = TOOLHEAD.get("axis_minimum")
    xmax, ymax, _, _ = TOOLHEAD.get("axis_maximum")

    x = np.mean([xmin, xmax])
    y = np.mean([ymin, ymax])
    return (x, y)


def get_random_loc(n=1, margin=50):
    xmin, ymin, _, _ = TOOLHEAD.get("axis_minimum")
    xmax, ymax, _, _ = TOOLHEAD.get("axis_maximum")

    out = []
    for _ in range(n):
        x = np.random.random() * (xmax - xmin - 2 * margin) + margin + xmin
        y = np.random.random() * (ymax - ymin - 2 * margin) + margin + ymin
        out.append((x, y))
    return out


def get_bed_corners() -> List:
    x_offset = CFG["probe"]["x_offset"]
    y_offset = CFG["probe"]["y_offset"]

    xmin, ymin = re.findall(r"[\d.]+", CFG["bed_mesh"]["mesh_min"])
    xmax, ymax = re.findall(r"[\d.]+", CFG["bed_mesh"]["mesh_max"])

    xmin = float(xmin) - float(x_offset)
    ymin = float(ymin) - float(y_offset)
    xmax = float(xmax) - float(x_offset)
    ymax = float(ymax) - float(y_offset)

    return [(xmin, ymax), (xmax, ymax), (xmin, ymin), (xmax, ymin)]


def move_to_loc(x, y, echo=False):
    move_to_safe_z()
    gcode = f"G0 X{x} Y{y} F99999"
    if echo:
        print(gcode)
        send_gcode(f"M118 {gcode}")
    send_gcode("G90")
    send_gcode(gcode)


def get_gcode_response(count=1000):
    url = f"{MOONRAKER_URL}/server/gcode_store?count={count}"
    gcode_resp = get(url).json()["result"]["gcode_store"]
    return gcode_resp


def test_probe(probe_count, loc=None, testname="", keep_first=False, **kwargs):
    "Send probe_accuracy command, and retrieve data from gcod respond cache"
    # breakpoint()
    if loc:
        move_to_loc(*loc)
    else:
        move_to_loc(*get_bed_center())

    start_time = get_gcode_response(count=1)[0]["time"]

    gcode_cmd = f"PROBE_ACCURACY SAMPLES={probe_count}"
    if kwargs.get("retract"):
        gcode_cmd += f' SAMPLE_RETRACT_DIST={kwargs["retract"]}'
    if kwargs.get("speed"):
        gcode_cmd += f' PROBE_SPEED={kwargs["speed"]}'
    send_gcode(gcode_cmd)
    raw = get_gcode_response(count=1000)
    gcode_resp = [x for x in raw if x["time"] > start_time]

    err_msgs = [x["message"] for x in gcode_resp if x["message"].startswith("!!")]
    msgs = [x["message"] for x in gcode_resp if x["message"].startswith("// probe at")]

    if len(err_msgs):
        print("\n\nSomething's wrong with probe_accuracy! Klipper response:")
        for msg in set(err_msgs):
            print(msg)
        check_klicky_macro_issue(err_msgs)

    data = []
    for i, msg in enumerate(msgs):
        coor = re.findall(r"[\d.]+", msg)
        x, y, z = [float(k) for k in coor]
        data.append({"test": testname, "sample_index": i, "x": x, "y": y, "z": z})

    if len(data) == 0:
        print("\nNo measurements collected")
        print("Exiting!")
        sys.exit(1)

    if CFG["probe"].get("drop_first_result") == "True" and not keep_first:
        data.pop(0)

    return pd.DataFrame(data)


def check_klicky_macro_issue(msgs):
    msg = "!! Error evaluating 'gcode_macro PROBE_ACCURACY:gcode': CommandError: Must perform PROBE_ACCURACY with the probe above the BED!"
    if msg in msgs:
        print("This issue can be fixed by updating klicky-macros.cfg")
        print(
            "Reference: https://github.com/jlas1/Klicky-Probe/commit/31a481c843567233c807bb310b6f0e83d60b4fca"
        )


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


def detect_probe():
    settings = query_printer_objects("configfile", "settings")
    user_variables = query_printer_objects("gcode_macro _User_Variables")

    try:
        if user_variables["docklocation_x"]:
            global isKlicky
            isKlicky = True
    except:
        True

    endstop_pin = CFG["stepper_z"]["endstop_pin"]
    if endstop_pin == "probe:z_virtual_endstop":
        global isTap
        isTap = True

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="""Automated probe testing.
    All three tests will run at default values unless individual tests are specified"""
    )
    ap.add_argument(
        "-c",
        "--corner",
        nargs="?",
        type=int,
        const=30,
        help="Enable corner test. Number of probe samples at each corner can be optionally provided. Default 30.",
    )
    ap.add_argument(
        "-r",
        "--repeatability",
        nargs="?",
        type=int,
        const=20,
        help="Enable repeatability test. Number of probe_accuracy tests can be optionally provided. Default 20.",
    )
    ap.add_argument(
        "-d",
        "--drift",
        nargs="?",
        type=int,
        const=100,
        help="Enable drift test. Number of probe_accuracy samples can be optionally provided. Default 100.",
    )
    ap.add_argument(
        "--speedtest",
        action="store_true",
        help="Enable probe speed test. Requires user input for speed parameters.",
    )
    ap.add_argument(
        "--export_csv",
        action="store_true",
        help="export data as csv",
    )
    ap.add_argument(
        "--force_dock",
        action="store_true",
        help="Force docking between tests. Default False",
    )
    ap.add_argument(
        "--keep_first",
        action="store_true",
        help="Keep first probe measurement",
    )
    ap.add_argument(
        "-s",
        "--speed",
        nargs="?",
        type=float,
        help="probe speed",
    )
    ap.add_argument(
        "--retract",
        nargs="?",
        type=float,
        help="probe sample retract distance",
    )
    ap.add_argument(
        "-u",
        "--update",
        action="store_true",
        help="Updates the script with git",
    )

    args = vars(ap.parse_args())

    if args["update"]:
        fetch_repo()
        sys.exit(0)

    main(args)
