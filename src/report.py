#!/usr/bin/env python3

import argparse
import subprocess
import os
import sys
import threading
import queue
import re
import datetime
import csv

from typing import List, Any, Tuple, Dict, Union, cast, NewType, Callable

from shutil import *
from format import format_goal, format_hypothesis
from yattag import Doc

Tag = Callable[..., Doc.Tag]
Text = Callable[..., None]
Line = Callable[..., None]

import serapi_instance
import linearize_semicolons
from serapi_instance import ParseError, LexError, TimeoutError
import tokenizer

from helper import *
from util import *
from context_filter import context_filters

from syntax import syntax_highlight, strip_comments

from predict_tactic import predictors, loadPredictor
from models.tactic_predictor import TacticPredictor

finished_queue = queue.Queue() # type: queue.Queue[int]
rows = queue.Queue() # type: queue.Queue[FileResult]
base = os.path.dirname(os.path.abspath(__file__)) + "/.."

details_css = ["details.css"]
details_javascript = ["details.js"]
report_css = ["report.css"]
report_js = ["report.js"]

num_predictions = 3
max_tactic_length = 100

baseline_tactic = "eauto"

predictorName : str
jobs : 'queue.Queue[str]'
num_jobs : int
net : TacticPredictor
gresult : 'GlobalResult'

def header(tag : Tag, doc : Doc, text : Text, css : List[str],
           javascript : List[str], title : str) -> None:
    with tag('head'):
        for filename in css:
            doc.stag('link', href=filename, rel='stylesheet')
        for filename in javascript:
            with tag('script', type='text/javascript',
                     src=filename):
                pass
        with tag('title'):
            text(title)

def details_header(tag : Any, doc : Doc, text : Text, filename : str) -> None:
    header(tag, doc, text, details_css, details_javascript,
           "Proverbot Detailed Report for {}".format(filename))

def report_header(tag : Any, doc : Doc, text : Text) -> None:
    header(tag, doc, text,report_css, report_js,
           "Proverbot Report")

def stringified_percent(total : float, outof : float) -> str:
    if outof == 0:
        return "NaN"
    else:
        return "{:10.2f}".format(total * 100 / outof)

def to_list_string(l : List[Any]) -> str:
    return "% ".join([str(item) for item in l])

def shorten_whitespace(string : str) -> str:
    return re.sub("    +", "  ", string)

def run_prediction(coq : serapi_instance.SerapiInstance, prediction : str) -> Tuple[str,str,Optional[Exception]]:
    prediction = prediction.lstrip("-+*")
    coq.quiet = True
    try:
        coq.run_stmt(prediction)
        context = coq.proof_context
        coq.cancel_last()
        assert isinstance(context, str)
        return (prediction, context, None)
    except (ParseError, LexError, BadResponse, CoqExn, TimeoutError) as e:
        return (prediction, "", e)
    finally:
        coq.quiet = False

# Warning: Mutates fresult
def evaluate_prediction(fresult : 'FileResult',
                        correct_command : str,
                        correct_result_context : str,
                        prediction_run : Tuple[str, str, Optional[Exception]]) -> Tuple[str, str]:
    prediction, context, exception = prediction_run
    grade = fresult.grade_command_result(prediction, context, correct_command,
                                         correct_result_context, exception)
    return (prediction, grade)

class GlobalResult:
    def __init__(self, options : List[Tuple[str, str]]) -> None:
        self.num_tactics = 0
        self.num_correct = 0
        self.num_partial = 0
        self.num_failed = 0
        self.num_topN = 0
        self.num_topNPartial = 0
        self.num_searched = 0
        self.lock = threading.Lock()
        self.options = options
        self.total_loss = 0.
        pass
    def add_file_result(self, result : 'FileResult'):
        self.lock.acquire()
        self.num_tactics += result.num_tactics
        self.num_correct += result.num_correct
        self.num_partial += result.num_partial
        self.num_failed += result.num_failed
        self.num_topN += result.num_topN
        self.num_topNPartial += result.num_topNPartial
        self.num_searched += result.num_searched
        self.total_loss += result.total_loss
        self.lock.release()
        pass
    def report_results(self, doc : Doc, text : Text, tag : Any, line : Line):
        with tag('h2'):
            text("Overall Accuracy: {}% ({}/{})"
                 .format(stringified_percent(self.num_searched, self.num_tactics),
                         self.num_searched, self.num_tactics))
        with tag('ul'):
            for k, v in self.options:
                with tag('li'):
                    text("{}: {}".format(k, v))
            with tag('li'):
                text("predictor: {}".format(predictorName))
        with tag('table'):
            with tag('tr', klass="header"):
                line('th', 'Filename')
                line('th', 'Number of Tactics in File')
                line('th', 'Number of Tactics Correctly Found')
                line('th', '% Correctly Found')
                line('th', '% Initially Correct')
                line('th', '% Top {}'.format(num_predictions))
                line('th', '% Partial')
                line('th', '% Top {} Partial'.format(num_predictions))
                line('th', 'Testing Loss')
                line('th', 'Details')
            sorted_rows = []
            while rows.qsize() > 0:
                sorted_rows.append(rows.get())
            sorted_rows = sorted(sorted_rows, key=lambda fresult: fresult.num_tactics,
                                 reverse=True)

            for fresult in sorted_rows:
                if fresult.num_tactics == 0:
                    continue
                with tag('tr'):
                    line('td', fresult.filename)
                    line('td', str(fresult.num_tactics))
                    line('td', str(fresult.num_searched))
                    line('td', stringified_percent(fresult.num_searched,
                                                   fresult.num_tactics))
                    line('td', stringified_percent(fresult.num_correct,
                                                   fresult.num_tactics))
                    line('td', stringified_percent(fresult.num_topN,
                                                   fresult.num_tactics))
                    line('td', stringified_percent(fresult.num_partial,
                                                   fresult.num_tactics))
                    line('td', stringified_percent(fresult.num_topNPartial,
                                                   fresult.num_tactics))
                    line('td', "{:10.2f}".format(fresult.total_loss / fresult.num_tactics))
                    with tag('td'):
                        with tag('a', href=fresult.details_filename() + ".html"):
                            text("Details")
            with tag('tr'):
                line('td', "Total");
                line('td', str(self.num_tactics))
                line('td', str(self.num_searched))
                line('td', stringified_percent(self.num_searched,
                                               self.num_tactics))
                line('td', stringified_percent(self.num_correct,
                                               self.num_tactics))
                line('td', stringified_percent(self.num_topN,
                                               self.num_tactics))
                line('td', stringified_percent(self.num_partial,
                                               self.num_tactics))
                line('td', stringified_percent(self.num_topNPartial,
                                               self.num_tactics))
                line('td', "{:10.2f}".format(self.total_loss / self.num_tactics))

    pass

def add_to_freq_table(table : Dict[Any, int], entry : Any) -> None:
    if entry not in table:
        table[entry] = 1
    else:
        table[entry] += 1

class FileResult:
    def __init__(self, filename : str) -> None:
        self.num_tactics = 0
        self.num_correct = 0
        self.num_partial = 0
        self.num_topN = 0
        self.num_topNPartial = 0
        self.num_searched = 0
        self.num_failed = 0
        self.filename = filename
        self.actual_tactic_frequency : Dict[str, int] = {}
        self.predicted_tactic_frequency : Dict[str, int] = {}
        self.correctly_predicted_frequency : Dict[str, int] = {}
        self.total_loss = 0.
        pass
    def grade_command_result(self, predicted : str, predicted_context : str,
                             actual : str, actual_context : str,
                             exception : Optional[Exception]) -> str:
        if actual.strip() == predicted.strip():
            return "goodcommand"
        elif (get_stem(actual) == get_stem(predicted)):
            return "okaycommand"
        elif type(exception) == ParseError or type(exception) == LexError:
            return "superfailedcommand"
        elif exception != None:
            return "failedcommand"
        elif predicted_context == actual_context:
            return "mostlygoodcommand"
        else:
            return "badcommand"
    def add_command_result(self,
                           predictions : List[str], grades : List[str],
                           actual : str, loss : float) -> None:
        add_to_freq_table(self.actual_tactic_frequency,
                          get_stem(actual))
        add_to_freq_table(self.predicted_tactic_frequency,
                          get_stem(predictions[0]))

        self.total_loss += loss

        self.num_tactics += 1
        if (grades[0] == "goodcommand" or grades[0] == "mostlygoodcommand"):
            add_to_freq_table(self.correctly_predicted_frequency,
                              get_stem(predictions[0]))
            self.num_correct += 1
            self.num_partial += 1
        elif (grades[0] == "okaycommand"):
            self.num_partial += 1
        elif (grades[0] == "failedcommand"):
            self.num_failed += 1

        for grade in grades:
            if (grade == "goodcommand" or grade == "mostlygoodcommand"):
                self.num_topN += 1
                self.num_topNPartial += 1
                break
            if (grade == "okaycommand"):
                self.num_topNPartial += 1
        for grade in grades:
            if (grade == "goodcommand" or grade == "mostlygoodcommand"):
                self.num_searched += 1
                break
            if grade != "failedcommand":
                break;
        pass

    def details_filename(self) -> str:
        return "{}".format(escape_filename(self.filename))
    pass

class Worker(threading.Thread):
    def __init__(self, workerid : int, coqargs : List[str], includes : str,
                 output_dir : str, prelude : str, debug : bool, num_jobs : int,
                 baseline : bool, context_filter : str) -> None:
        threading.Thread.__init__(self, daemon=True)
        self.coqargs = coqargs
        self.includes = includes
        self.workerid = workerid
        self.output_dir = output_dir
        self.prelude = prelude
        self.debug = debug
        self.num_jobs = num_jobs
        self.baseline = baseline
        self.cfilter = context_filters[context_filter]
        pass

    def get_commands(self, filename : str) -> List[str]:
        local_filename = self.prelude + "/" + filename
        loaded_commands = linearize_semicolons.try_load_lin(local_filename)
        if loaded_commands is None:
            fresh_commands = lift_and_linearize(
                load_commands_preserve(self.prelude + "/" + filename),
                self.coqargs, self.includes, self.prelude,
                filename, debug=self.debug)
            linearize_semicolons.save_lin(fresh_commands, local_filename)
            return fresh_commands
        else:
            return loaded_commands

    def process_file(self, filename : str) -> None:
        global gresult
        fresult = FileResult(filename)

        if self.debug:
            print("Preprocessing...")
        commands = self.get_commands(filename)

        command_results : List[Union[Tuple[str],
                                     Tuple[str, str, str,
                                           List[Tuple[str, str]]]]] = []

        with serapi_instance.SerapiContext(self.coqargs,
                                           self.includes,
                                           self.prelude) as coq:
            coq.debug = self.debug
            nb_commands = len(commands)
            for i in range(nb_commands):
                command = commands[i]
                # print("Processing command {}/{}".format(str(i+1), str(nb_commands)))
                in_proof = (coq.proof_context and
                            not re.match(".*Proof.*", command.strip()))
                if in_proof:
                    hyps = coq.get_hypothesis()
                    goals = coq.get_goals()
                    if self.baseline:
                        predictions = [baseline_tactic + "."] * num_predictions
                    else:
                        predictions, loss = net.predictKTacticsWithLoss(
                            {"goal" : format_goal(goals),
                             "hyps" : format_hypothesis(hyps)},
                            num_predictions,
                            command);

                    prediction_runs = [run_prediction(coq, prediction) for
                                       prediction in predictions]

                    try:
                        coq.run_stmt(command)
                        actual_result_context = coq.proof_context
                        actual_result_goal = coq.get_goals()
                        actual_result_hypothesis = coq.get_hypothesis()
                        assert isinstance(actual_result_context, str)
                    except (AckError, CompletedError, CoqExn,
                            BadResponse, ParseError, LexError, TimeoutError):
                        print("In file {}:".format(filename))
                        raise

                    prediction_results = [evaluate_prediction(fresult, command,
                                                              actual_result_context,
                                                              prediction_run)
                                          for prediction_run in prediction_runs]
                    if self.cfilter({"goal": format_goal(goals),
                                     "hyps": format_hypothesis(hyps)},
                                    command,
                                    {"goal": format_goal(actual_result_goal),
                                     "hyps":
                                     format_hypothesis(actual_result_hypothesis)}):
                        fresult.add_command_result(
                            [pred for pred, ctxt, ex in prediction_runs],
                            [grade for pred, grade in prediction_results],
                            command, loss)

                        command_results.append((command, hyps, goals,
                                                prediction_results))
                    else:
                        command_results.append((command,))
                else:
                    try:
                        coq.run_stmt(command)
                    except (AckError, CompletedError, CoqExn,
                            BadResponse, ParseError, LexError, TimeoutError):
                        print("In file {}:".format(filename))
                        raise
                    command_results.append((command,))

        with open("{}/{}.csv".format(self.output_dir, fresult.details_filename()),
                  'w', newline='') as csvfile:
            rowwriter = csv.writer(csvfile, lineterminator=os.linesep)
            for row in command_results:
                if len(row) == 1:
                    rowwriter.writerow([re.sub("\n", "\\n", row[0])])
                else:
                    command, hyps, goal, prediction_results = \
                        cast(Tuple[str, str, str, List[Tuple[str, str]]], row)
                    first_pred, first_grade = prediction_results[0]
                    if len(prediction_results) >= 2:
                        second_pred, second_grade = prediction_results[1]
                    else:
                        second_pred, second_grade = "", ""
                    if len(prediction_results) >= 3:
                        third_pred, third_grade = prediction_results[2]
                    else:
                        third_pred, third_grade = "", ""
                    rowwriter.writerow([re.sub("\n", "\\n", item) for item in
                                        [command, hyps, goal,
                                         first_pred, first_grade,
                                         second_pred, second_grade,
                                         third_pred, third_grade]])

        doc, tag, text, line = Doc().ttl()

        with tag('html'):
            details_header(tag, doc, text, filename)
            with tag('div', id='overlay', onclick='event.stopPropagation();'):
                with tag('div', id='predicted'):
                    pass
                with tag('div', id='context'):
                    pass
                with tag('div', id='stats'):
                    pass
                pass
            with tag('body', onclick='deselectTactic()',
                     onload='setSelectedIdx()'), tag('pre'):
                for idx, command_result in enumerate(command_results):
                    if len(command_result) == 1:
                        with tag('code', klass='plaincommand'):
                            text(command_result[0])
                    else:
                        command, hyps, goal, prediction_results = \
                            cast(Tuple[str, str, str, List[Tuple[str, str]]],
                                 command_result)
                        predictions = [prediction for prediction, grade in
                                       prediction_results]
                        grades = [grade for prediction, grade in
                                  prediction_results]
                        search_index = 0
                        for pidx, prediction_result in enumerate(prediction_results):
                            prediction, grade = prediction_result
                            if (grade != "failedcommand" and
                                grade != "superfailedcommand"):
                                search_index = pidx
                                break
                        with tag('span',
                                 ('data-hyps',hyps),
                                 ('data-goal',shorten_whitespace(goal)),
                                 ('data-num-total', str(fresult.num_tactics)),
                                 ('data-predictions',
                                  to_list_string(predictions)),
                                 ('data-num-predicteds',
                                  to_list_string([fresult.predicted_tactic_frequency
                                                  .get(get_stem(prediction), 0)
                                                  for prediction in predictions])),
                                 ('data-num-corrects',
                                  to_list_string([fresult.correctly_predicted_frequency
                                                  .get(get_stem(prediction), 0)
                                                  for prediction in predictions])),
                                 ('data-num-actual-corrects',
                                  fresult.correctly_predicted_frequency
                                  .get(get_stem(command), 0)),
                                 ('data-num-actual-in-file',
                                  fresult.actual_tactic_frequency
                                  .get(get_stem(command))),
                                 ('data-actual-tactic',
                                  strip_comments(command)),
                                 ('data-grades',
                                  to_list_string(grades)),
                                 ('data-search-idx',
                                  search_index),
                                 id='command-' + str(idx),
                                 onmouseover='hoverTactic({})'.format(idx),
                                 onmouseout='unhoverTactic()',
                                 onclick='selectTactic({}); event.stopPropagation();'
                                 .format(idx)):
                            doc.stag("br")
                            for idx, prediction_result in enumerate(prediction_results):
                                prediction, grade = prediction_result
                                if search_index == idx:
                                    with tag('code', klass=grade):
                                        text(" " + command.strip())
                                else:
                                    with tag('span', klass=grade):
                                        doc.asis(" &#11044;")

        with open("{}/{}.html".format(self.output_dir, fresult.details_filename()), "w") as fout:
            fout.write(syntax_highlight(doc.getvalue()))

        gresult.add_file_result(fresult)
        rows.put(fresult)
    def run(self) -> None:
        try:
            while(True):
                job = jobs.get_nowait()
                jobnum = num_jobs - jobs.qsize()
                print("Processing file {} ({} of {})".format(job,
                                                             jobnum,
                                                             num_jobs))
                self.process_file(job)
                print("Finished file {} ({} of {})".format(job,
                                                           jobnum,
                                                           num_jobs))
        except queue.Empty:
            pass
        finally:
            finished_queue.put(self.workerid)

def escape_filename(filename : str) -> str:
    return re.sub("/", "Zs", re.sub("\.", "Zd", re.sub("Z", "ZZ", filename)))

def main(arg_list : List[str]) -> None:
    global jobs
    global num_jobs
    global net
    global gresult
    global predictorName
    parser = argparse.ArgumentParser(description=
                                     "try to match the file by predicting a tactic")
    parser.add_argument('-j', '--threads', default=1, type=int)
    parser.add_argument('--prelude', default=".")
    parser.add_argument('--debug', default=False, const=True, action='store_const')
    parser.add_argument('-o', '--output', help="output data folder name",
                        default="report")
    parser.add_argument('-m', '--message', default=None)
    parser.add_argument('--baseline',
                        help="run in baseline mode, predicting {} every time"
                        .format(baseline_tactic),
                        default=False, const=True, action='store_const')
    parser.add_argument('--no-context-filter',
                        help="Don't filter data pairs the way it was done in training.",
                        dest="use_context_filter",
                        default=True, const=False, action='store_const')
    parser.add_argument('--weightsfile', default="data/pytorch-weights.tar")
    parser.add_argument('--predictor', choices=list(predictors.keys()), default=list(predictors.keys())[0])
    parser.add_argument('filenames', nargs="+", help="proof file name (*.v)")
    args = parser.parse_args(arg_list)

    coqargs = ["{}/coq-serapi/sertop.native".format(base),
               "--prelude={}/coq".format(base)]
    includes = subprocess.Popen(['make', '-C', args.prelude, 'print-includes'],
                                stdout=subprocess.PIPE).communicate()[0].decode('utf-8')

    # Get some metadata
    cur_commit = subprocess.check_output(["git show --oneline | head -n 1"],
                                         shell=True).decode('utf-8').strip()
    cur_date = datetime.datetime.now()

    if not os.path.exists(args.output):
        os.makedirs(args.output)

    jobs = queue.Queue()
    workers = []
    num_jobs = len(args.filenames)
    for infname in args.filenames:
        jobs.put(infname)

    args.threads = min(args.threads, len(args.filenames))

    net = loadPredictor({"filename": args.weightsfile,
                         "beam-width": num_predictions ** 2},
                        args.predictor)
    predictorName = args.predictor
    gresult = GlobalResult(net.getOptions())

    for idx in range(args.threads):
        worker = Worker(idx, coqargs, includes, args.output,
                        args.prelude, args.debug, num_jobs,
                        args.baseline,
                        dict(net.getOptions())["context filter"]
                        if args.use_context_filter else "all")
        worker.start()
        workers.append(worker)

    for idx in range(args.threads):
        finished_id = finished_queue.get()
        workers[finished_id].join()
        print("Thread {} finished ({} of {}).".format(finished_id, idx + 1, args.threads))

    ###
    ### Write the report page out
    ###

    doc, tag, text, line = Doc().ttl()

    with tag('html'):
        report_header(tag, doc, text)
        with tag('body'):
            with tag('h4'):
                text("{} files processed".format(num_jobs))
            with tag('h5'):
                text("Commit: {}".format(cur_commit))
            if args.message:
                with tag('h5'):
                    text("Message: {}".format(args.message))
            if args.baseline:
                with tag('h5'):
                    text("Baseline build!! Always predicting {}".format(baseline_tactic))
            with tag('h5'):
                text("Run on {}".format(cur_date.strftime("%Y-%m-%d %H:%M:%S.%f")))
            with tag('img',
                     ('src', 'logo.png'),
                     ('id', 'logo')):
                pass
            gresult.report_results(doc, text, tag, line)

    extra_files = ["report.css", "details.css", "details.js", "logo.png", "report.js"]

    for filename in extra_files:
        copy(base + "/reports/" + filename, args.output + "/" + filename)

    with open("{}/report.html".format(args.output), "w") as fout:
        fout.write(doc.getvalue())