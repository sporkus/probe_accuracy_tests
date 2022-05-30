# Automating probe accuracy testing

`probe_accuracy_test_suite.py` is a collection of tests to help checking probe accuracy, precision and drift under different conditions.

### Tests Included

* 1 test, 30 samples at each bed mesh corners - check if there are issues with individual z drives. 
* 20 tests, 5 samples at bed center - check consistency within normal measurements
* 1 test, 100 samples at bed center - check for drift

### How to 

#### Running all tests
`python3 probe_accuracy_test_suite.py`
#### Running individual test
Use `python3 probe_accuracy_test_suite.py -h` to see all the options
Example: `python3 probe_accuracy_test_suite.py --corner`


### Installation

On your printer:
```
curl -sSL https://raw.githubusercontent.com/sporkus/probe_accuracy_tests/master/install.sh | bash
```


### Requirements

#### Python

See requirements.txt
If they are not installed - they can be installed with `pip3 install -r requirements.txt`

#### Printer

* Need klicky macros properly configured, so that homing/leveling/probe accuracy gcodes
will pick up the probe safely.

### Output

All collected measurements and summarized data are exported as csv for your analysis.
(You can see my printer not working too well :D)

Plots:

![](drift.png)
![](repeat.png)
![](repeat1.png)
![](corner.png)
![](corner2.png)

Terminal:

![](terminal.png)

