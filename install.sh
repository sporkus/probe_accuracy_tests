#!/usr/bin/bash

cd "${HOME}" || exit
echo "Cloning repo"
git clone https://github.com/sporkus/probe_accuracy_tests
cd "${HOME}"/probe_accuracy_tests || exit
echo "Install python packages"
python3 -c "import sys, pkgutil; sys.exit(0 if pkgutil.find_loader(sys.argv[1]) else 1)" pip || python3 -m pip install --upgrade
pip3 install -r requirements.txt
echo "Installation finished"

printf "\nUsage instructions:  python3 %s/probe_accuracy_tests/probe_accuracy_test_suite.py -h" "${HOME}"
printf "\nMore details and updated instructions: \n\thttps://github.com/sporkus/probe_accuracy_tests/blob/master/README.md\n"