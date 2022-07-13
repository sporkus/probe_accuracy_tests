#!/usr/bin/env bash
REPO=probe_accuracy_tests

cd "${HOME}" || exit
# Clone repo if it doesn't exist
[ -d "${REPO}" ] || git clone https://github.com/sporkus/probe_accuracy_tests 
cd "${REPO}"; git pull

# Install pip if it doesn't exist 
[ -x "$(command -v pip3)" ] || sudo apt install python3-pip

echo "Installing python packages"
pip3 install -r requirements.txt --upgrade

printf "\nUsage instructions:  python3 %s/probe_accuracy_tests/probe_accuracy_test_suite.py -h" "${HOME}"
printf "\nMore details and updated instructions: \n\thttps://github.com/sporkus/probe_accuracy_tests/blob/master/README.md\n"
