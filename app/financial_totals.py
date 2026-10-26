"""Shared sales/receipt definitions; callers supply authorized order scope only."""

def net_sales(gross, refunds, amendment_refunds=0):
    # Amended totals already include the price reduction. Returning the excess
    # payment reduces receipts, but must not deduct the same sale twice.
    return int(gross)-int(refunds)+int(amendment_refunds)


def period_totals(c, where, params):
    """Internal canonical scope with two creation-date bounds, never client SQL.

    Sales follow completion; payment/refund amounts follow their event time.
    Complimentary and test-void orders are excluded just as in accounting sales.
    """
    scope=where+" AND NOT (o.snapshot ? 'complimentary')"
    sales_scope=scope.replace('o.created_at>=','o.completed_at>=').replace('o.created_at<','o.completed_at<')
    sales=c.execute("SELECT coalesce(sum(o.total_halalas),0) gross FROM orders o WHERE "+sales_scope,params).fetchone()
    refund_scope=scope.replace('o.created_at>=','r.created_at>=').replace('o.created_at<','r.created_at<')
    refunds=c.execute("SELECT coalesce(sum(r.amount_halalas),0) refunded,coalesce(sum(r.amount_halalas) FILTER(WHERE r.amendment_id IS NOT NULL),0) amendments FROM refunds r JOIN orders o ON o.id=r.order_id WHERE "+refund_scope,params).fetchone()
    collection_scope=scope.replace('o.created_at>=','p.created_at>=').replace('o.created_at<','p.created_at<')
    payments=c.execute('SELECT p.method,coalesce(sum(p.amount_halalas),0) amount_halalas FROM collections p JOIN orders o ON o.id=p.order_id WHERE '+collection_scope+' GROUP BY p.method',params).fetchall()
    return {'gross_completed_halalas':int(sales['gross']),
            'refund_halalas':int(refunds['refunded']),
            'net_completed_halalas':net_sales(sales['gross'],refunds['refunded'],refunds['amendments']),
            'net_receipts_halalas':sum(int(p['amount_halalas']) for p in payments)-int(refunds['refunded'])}
