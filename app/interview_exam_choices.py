from random import SystemRandom
from .interview_bank import private_bank

def choice_paper():
    bank = private_bank()
    rng = SystemRandom()
    tasks = []
    for case_number, (title, prompt, questions) in enumerate(bank["cases"], 1):
        fields, choices, explanations, partial = [], {}, {}, {}
        for index, (question, best, *rest) in enumerate(questions, 1):
            key = f'q{case_number}_{index}'
            alternatives = [best, *rest[:-1]]
            eligible = [alternatives[i-1] for i in bank["partial"][case_number-1][index-1]]
            rng.shuffle(alternatives)
            partial[key] = [alternatives.index(option)+1 for option in eligible]
            fields.append([key, question, alternatives.index(best) + 1, 1])
            choices[key] = alternatives
            explanations[key] = rest[-1]
        tasks.append(dict(sequential=True, title=title, prompt=prompt, fields=fields, choices=choices, explanations=explanations, partial=partial))
    return tasks
