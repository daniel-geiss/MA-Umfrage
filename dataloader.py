
from typing import Literal
import pandas as pd

from datasets import Dataset
import json
import re


_data = pd.read_parquet('./data/survey_data.parquet')

PART_2_IDS = [2125122, 2228380, 2528332, 1226257, 2320418, 2263574, 2092381, 1922629, 2716217, 2023401, 2717657, 920114, 2387118, 2236750, 2236750, 2481533, 2535616, 971492, 2193298, 2314902, 1087818, 2524295, 2267402, 2689705, 2222702]

PART_1_IDS = [2359141, 1942195, 2058800, 1867709, 1400535, 2111384, 2585517, 1179280, 2623699, 2351679, 2289256, 1135939, 1125352, 2521139, 1180213, 2498446, 2077077, 1357520, 1402373, 2225678]

def _strip_id(ex_id):
    return re.match(r'[A|B|C][1|2]_\d\d_\d\d', ex_id).group()

def _collapse_whitespace(text):
     return re.sub(r'\s*\n\s*', '\n',text).strip()

def _cleanup(text:str):
    return text.replace('*','')

def get_part_2():

    samples = _data.set_index('submission_id').loc[PART_2_IDS].reset_index().copy()
    assert(len(samples) == len(PART_2_IDS))
    samples = samples[['submission_id','cleantext','numeric_grade', 'exercise_id', 'course_level']]

    mapper = {'exercise_id':'id','cleantext':'example_text','course_level':'level'}

    samples = samples.rename(columns=mapper)

    samples['id'] = samples['id'].map(_strip_id)
    samples['example_text'] = samples['example_text'].map(str.strip)
    return samples.to_dict(orient='records')

def get_part_1():
    samples = _data.set_index('submission_id').loc[PART_1_IDS].reset_index().copy()
    
    #check for missing samples and if ids match
    assert(len(samples) == len(PART_1_IDS))
    assert(all( id_df == id_ls for id_df, id_ls in zip(samples['submission_id'],PART_1_IDS)))
    
    samples = samples[['submission_id','cleantext','numeric_grade', 'exercise_id', 'course_level', 'llm_grade', 'llm_reason']]

    samples['comment'] = samples['llm_grade'].map(lambda g: g['comment'])
    samples['grading'] = samples['llm_grade'].map(lambda g: g['note'])

    mapper = {'exercise_id':'id','cleantext':'example_text','course_level':'level'}

    samples = samples.rename(columns=mapper)

    samples = samples[['id','example_text','level','grading','comment', 'llm_reason']]

    samples['reasoning'] = [open(f'./data/clipped_reasoning/{id}.txt').read() for id in PART_1_IDS]
    
#    for b in [clip in r for clip,r in zip(samples['reasoning'], samples['llm_reason'])]:
#        print(b)

    samples['id'] = samples['id'].map(_strip_id)
    samples['example_text'] = samples['example_text'].map(str.strip)
    samples['reasoning'] = samples['reasoning'].map(lambda r: _collapse_whitespace(_cleanup(r)))
    return samples.to_dict(orient='records')


if __name__ == "__main__":

