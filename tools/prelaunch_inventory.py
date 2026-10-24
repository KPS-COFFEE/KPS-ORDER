"""Run inside the existing private service. Produces no mutation or deletion."""
import argparse
import json
from pathlib import Path
from app import db
from app.prelaunch_inventory import inventory
from tools.portable_backup import private_write


def main():
    parser=argparse.ArgumentParser(description='Read-only prelaunch dry-run inventory; no reset option exists')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        report=inventory(conn)
    private_write(Path(args.output),json.dumps(report,ensure_ascii=False,sort_keys=True,default=str).encode())
    print('Read-only inventory saved. Reset remains BLOCKED pending independent backup, restoration and legal review.')

if __name__=='__main__':
    main()
