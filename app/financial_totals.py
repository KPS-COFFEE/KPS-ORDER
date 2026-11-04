"""Shared sales/receipt definitions; callers supply authorized order scope only."""


def net_sales(gross, refunds, amendment_refunds=0):
    # Amended totals already include the price reduction. Returning the excess
    # payment reduces receipts, but must not deduct the same sale twice.
    return int(gross)-int(refunds)+int(amendment_refunds)


def sales_adjustment_at(completed_at, movement_at):
    """A repayment/credit cannot reverse revenue before revenue was recognized.

    Cash always keeps its actual movement date. Once an order completes, earlier
    ordinary refunds and price credits reduce its sale at completion; later
    adjustments reduce sales on their own date. Cancelled unfulfilled orders
    never produce a sales reversal.
    """
    return max(completed_at, movement_at) if completed_at else None


def _at(where, column):
    # Internal scopes only: replace the two known creation-time bounds while
    # retaining every tenant, branch, service and manager predicate unchanged.
    return where.replace('o.created_at>=', column+'>=',1).replace('o.created_at<', column+'<',1)


def _sales_movements(where):
    scope=where+" AND NOT (o.snapshot ? 'complimentary')"
    sales_scope=_at(where,'o.completed_at')
    refund_scope=_at(scope,'GREATEST(o.completed_at,r.created_at)')
    credit_scope=_at(scope,'GREATEST(o.completed_at,a.created_at)')
    return """SELECT o.branch_id,1 completed,
        CASE WHEN o.snapshot ? 'complimentary' THEN 0 ELSE o.total_halalas END gross,
        CASE WHEN o.snapshot ? 'complimentary' THEN 0 ELSE o.delivery_fee_halalas END delivery_fee,
        0::bigint sales_refunded,0::bigint price_credit
        FROM orders o WHERE """+sales_scope+"""
      UNION ALL SELECT o.branch_id,0,0,0,r.amount_halalas,0
        FROM refunds r JOIN orders o ON o.id=r.order_id
        WHERE o.completed_at IS NOT NULL AND r.amendment_id IS NULL
        AND """+refund_scope+"""
      UNION ALL SELECT o.branch_id,0,0,0,0,(a.details->>'credit_halalas')::bigint
        FROM audit_events a JOIN orders o ON o.id=a.entity_id
        WHERE o.completed_at IS NOT NULL AND a.entity_type='order'
        AND a.action='accounting.order_financial_treatment.approved'
        AND a.details->>'kind'='price_correction' AND """+credit_scope


def period_branch_totals(c, where, params, limit=20):
    """Same recognized sales ledger as the summary, grouped by execution branch."""
    return c.execute("WITH sales_movements AS ("+_sales_movements(where)+""")
      SELECT m.branch_id,b.branch_no,b.name,sum(m.completed) completed,
        sum(m.gross) gross_halalas,sum(m.sales_refunded) sales_refund_halalas,
        sum(m.price_credit) recognized_price_credit_halalas,
        sum(m.gross-m.sales_refunded-m.price_credit) net_halalas
      FROM sales_movements m JOIN branches b ON b.id=m.branch_id
      GROUP BY m.branch_id,b.branch_no,b.name ORDER BY completed DESC,m.branch_id LIMIT %s""",
      [*params,*params,*params,limit]).fetchall()


def period_totals(c, where, params):
    """Internal canonical scope with two creation-date bounds, never client SQL.

    Sales use completion; cash refunds/collections keep their actual event date.
    A refund of a never-completed order is only a repayment of collected money.
    """
    scope=where+" AND NOT (o.snapshot ? 'complimentary')"
    sales=c.execute("WITH sales_movements AS ("+_sales_movements(where)+""")
      SELECT coalesce(sum(completed),0) completed,coalesce(sum(gross),0) gross,
        coalesce(sum(delivery_fee),0) delivery_fee,
        coalesce(sum(sales_refunded),0) sales_refunded,
        coalesce(sum(price_credit),0) price_credit FROM sales_movements""",
      [*params,*params,*params]).fetchone()
    refund_scope=_at(scope,'r.created_at')
    refunds=c.execute("SELECT coalesce(sum(r.amount_halalas),0) refunded FROM refunds r JOIN orders o ON o.id=r.order_id WHERE "+refund_scope,params).fetchone()
    collection_scope=_at(scope,'p.created_at')
    payments=c.execute('SELECT p.method,coalesce(sum(p.amount_halalas),0) amount_halalas FROM effective_collections p JOIN orders o ON o.id=p.order_id WHERE '+collection_scope+' GROUP BY p.method',params).fetchall()
    credit_scope=_at(scope,'a.created_at')
    credits=c.execute("SELECT coalesce(sum((a.details->>'credit_halalas')::bigint),0) amount FROM audit_events a JOIN orders o ON o.id=a.entity_id WHERE a.entity_type='order' AND a.action='accounting.order_financial_treatment.approved' AND a.details->>'kind'='price_correction' AND "+credit_scope,params).fetchone()
    return {'completed':int(sales['completed']),
            'delivery_fee_halalas':int(sales['delivery_fee']),
            'gross_completed_halalas':int(sales['gross']),
            'price_credit_halalas':int(credits['amount']),
            'recognized_price_credit_halalas':int(sales['price_credit']),
            'sales_refund_halalas':int(sales['sales_refunded']),
            'refund_halalas':int(refunds['refunded']),
            'net_completed_halalas':net_sales(sales['gross'],sales['sales_refunded'])-int(sales['price_credit']),
            'net_receipts_halalas':sum(int(p['amount_halalas']) for p in payments)-int(refunds['refunded'])}
