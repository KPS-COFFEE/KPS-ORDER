def candidate_copy(tasks):
    return [{**{k:v for k,v in task.items() if k not in ('explanations','partial')}, 'fields':[field[:2] for field in task['fields']]} for task in tasks]


def grade(tasks, answers):
    details = []
    sections = []
    for task in tasks:
        start = len(details)
        for key, label, expected, points in task['fields']:
            value = answers.get(key, '').strip()
            normalized = value.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789'))
            earned = points if normalized and normalized.isdecimal() and int(normalized)==expected else 0
            if normalized in ('1','2','3','4','5') and int(normalized) in task.get('partial', {}).get(key, []):
                earned = points / 2
            detail = dict(label=label,answer=value,expected=expected,earned=earned,maximum=points)
            if key in task.get('choices', {}):
                choices = task['choices'][key]
                detail['answer'] = choices[int(value)-1] if value in ('1','2','3','4','5') else ''
                detail['expected'] = choices[expected-1]
                detail['explanation'] = task['explanations'][key]
            details.append(detail)
        rows = details[start:]
        sections.append(dict(title=task['title'], points=sum(r['earned'] for r in rows),
                             maximum=sum(r['maximum'] for r in rows)))
    return {'sections':sections,'points':sum(row['earned'] for row in details),'maximum':sum(row['maximum'] for row in details),'details':details}
