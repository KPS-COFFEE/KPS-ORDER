"""Item quantities separate completed sales from gifts and free fulfilment."""


def new_item(product_id, name):
    return dict(product_id=product_id,label=name,quantity=0,returned_quantity=0,gross=0,correct=0,processed=0,
                gift_quantity=0,hospitality_quantity=0,advertising_quantity=0,compensation_quantity=0,zero_value_quantity=0)


def add_line(row, item, order):
    quantity=int(item['quantity'])
    kind=(order['snapshot'].get('complimentary') or {}).get('kind')
    if kind in ('hospitality','advertising','compensation'):
        row[kind+'_quantity']+=quantity
    elif item.get('is_gift'):
        row['gift_quantity']+=quantity
    elif order['total_halalas']==0 or int(item.get('line_total_halalas',0))==0:
        row['zero_value_quantity']+=quantity
    else:
        row['quantity']+=quantity
        row['gross']+=int(item['line_total_halalas'])
        return True
    return False
