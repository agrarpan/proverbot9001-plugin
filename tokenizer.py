#!/usr/bin/env python3

import re

debug_tokenizer = False

def next_char(c):
    return chr(ord(c) + 1)

def make_fresh():
    next = chr(128)#"\uAC00" # Hangul syllables (~11k symbols)
    def fresh():
        nonlocal next
        curr = next
        next = next_char(next)
        return curr
    return fresh

fresh = make_fresh()

patterns = [
    "apply",
    "assert",
    "eauto",
    "auto",
    "case",
    "clear",
    "destruct",
    "discriminate",
    "eapply",
    "first",
    "generalize",
    "induction",
    "intros",
    "intro",
    "intuition",
    "inversion",
    "inv",
    "reflexivity",
    "revert",
    "rewrite",
    "transitivity",
    "unfold",
    "with",
    "set",
    "simpl",
    "try",
    "congruence",
    "omega",
    "repeat"
    "as",
    "using",
    "exact",
]

num_tokenizer_patterns = len(patterns)

tokens = map(lambda p: (p, fresh()), patterns)

# Two dictionaries for fast lookup both ways:

dict_pattern_to_token = {}
dict_token_to_pattern = {}
for (p, t) in tokens:
    dict_pattern_to_token[p] = t
    dict_token_to_pattern[t] = p

def pattern_to_token(s):
    s_in = s
    for k in dict_pattern_to_token:
        s = re.sub("(^|(?<=[ ])){}(?=[ ]|;|.)".format(k), dict_pattern_to_token[k], s)
    if debug_tokenizer:
        print("{} -> {}".format(s_in, [ord(c) for c in s]))
    return s

def token_to_pattern(s):
    s_in = s
    for k in dict_token_to_pattern:
        s = re.sub("(^|(?<=[ ])){}(?=[ ]|;|.)".format(k), dict_token_to_pattern[k], s)
    if debug_tokenizer:
        print("{} -> {}".format([ord(c) for c in s_in], s))
    return s