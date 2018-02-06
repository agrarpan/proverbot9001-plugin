#!/usr/bin/env python3

import subprocess
import re
from subprocess import run
from datetime import datetime

def get_lines(command):
    return (run([command], shell=True, stdout=subprocess.PIPE)
            .stdout.decode('utf-8').split('\n')[:-1])

def get_file_date(filename):
    return datetime.strptime(filename.split("/")[1].split("+")[0], '%Y-%m-%dT%Hd%Md%S%z')

def get_file_percent(filename):
    with open(filename+"/report.html") as f:
        contents = f.read()
        oldPercentString = re.search(r"Overall Accuracy:\s+(\d+\.\d+)%", contents)
        if oldPercentString:
            return float(oldPercentString.group(1))
        else:
            return float(re.search(r"Searched:\s+(\d+\.\d+)%", contents).group(1))


files = sorted(get_lines("find -type d -not -name '.*'"), key=lambda f: get_file_date(f), reverse=True)
with open('index.md', 'w') as index:
    index.write("% Proverbot9001 Reports\n"
                "%\n"
                "%\n"
                "\n")
    index.write("---\n"
                "header-includes: <script src='index.js'></script>"
                                 "<script src='https://d3js.org/d3.v4.min.js'></script>\n"
                "---\n")
    index.write("<svg width='700' height='400' style='border-style:outset; border-color:#00ffff'></svg>\n\n")
    index.write("|Date|Overall Accuracy||\n"
                "|----|----|----|\n")
    for f in files:
        index.write("| {} |{}%|[Link]({}/report.html)|\n".format(get_file_date(f).ctime(), get_file_percent(f), f))

assert(run(["pandoc index.md -s --css index.css > index.html"], shell=True))