"""One-off backfill of PSN play time from the Home Assistant recorder database (opened read-only).
Usage: python3 psn_backfill.py <recorder.db> <now-playing sensor entity> <out psn_playtime_<player>.json> <since YYYY-MM-DD> [--write]
Prints the totals; writes the file only with --write. Totals count from the <since> date."""
import sys, os, sqlite3, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from providers import psn_sessions as P

WRITE_FLAG = "--write"
# Not a game tool: no NAME/matches, so tools.all_tools() skips this module.


def main(db, entity, out, since_date, write=False):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = c.execute("select s.state, s.last_updated_ts from states s join states_meta m on s.metadata_id=m.metadata_id "
                     "where m.entity_id=? order by s.last_updated_ts", (entity,)).fetchall()
    since = datetime.datetime.fromisoformat(since_date).replace(tzinfo=datetime.timezone.utc).timestamp()
    st, data = {}, {"games": {}, "tracked_since": since_date}
    for state, ts in rows:
        if ts is None or ts < since:
            continue
        P.step(st, data, P.reading(state), datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(timespec="seconds"))
    data["state"] = st
    for g, t in sorted(data["games"].items()):
        print("%-30s %5d min (%d sessions)" % (g, t["minutes"], t["sessions"]))
    if write:
        P._write(out, data)
        print("written", out)
    return data


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != WRITE_FLAG]
    main(a[0], a[1], a[2], a[3], WRITE_FLAG in sys.argv)
