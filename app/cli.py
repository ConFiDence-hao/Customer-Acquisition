"""Operator commands. Run only when no collector process is active."""
import argparse
import json
import sqlite3
from . import db,clock
from datetime import datetime,timezone
from .collectors import register_sources,run_collection

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['init','collect','status','backup'])
    args=p.parse_args()
    if args.command in ('init','collect'):
        clock.initialize();db.initialize();register_sources()
    if args.command=='collect':
        with db.connect() as c:ident=c.execute("INSERT INTO runs(started_at,status) VALUES(?,'运行中')",(db.now(),)).lastrowid
        run_collection(ident)
    if args.command=='backup':
        destination=db.DATA/'backups';destination.mkdir(exist_ok=True)
        path=destination/('radar_'+datetime.now(timezone.utc).isoformat().replace(':','').replace('+','_')+'.db')
        with db.connect() as source,sqlite3.connect(path) as target:source.backup(target)
        print('Backup complete:',path.name)
    with db.connect() as c:
        print(json.dumps({'companies':c.execute('SELECT COUNT(*) FROM companies').fetchone()[0],'opportunities':c.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0],'runs':[dict(x) for x in c.execute('SELECT id,started_at,status,found,inserted,updated FROM runs ORDER BY id DESC LIMIT 3')]},ensure_ascii=False))

if __name__=='__main__':main()
