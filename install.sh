#!/usr/bin/bash

cd ${HOME}
echo "Cloning repo"
git clone https://github.com/sporkus/probe_accuracy_tests
cd ${HOME}/probe_accuracy_tests
echo "Install python packages"
pip3 install -r requirements.txt
echo "Installation finished"

printf "\nUsage instructions:  python3 ${HOME}/probe_accuracy_tests/probe_accuracy_test_suite.py -h"
printf "\nMore details and updated instructions: https://github.com/sporkus/probe_accuracy_tests/blob/master/README.md\n"