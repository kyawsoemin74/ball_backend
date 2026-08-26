import os, re
from sqlalchemy import create_engine, text

url = os.getenv('DATABASE_URL', 'postgresql://fover_user:242374@localhost:5432/fover_db')
if url.startswith('postgres://'):
    url = url.replace('postgres://', 'postgresql://', 1)
engine = create_engine(url)
with engine.connect() as conn:
    league_ids = {r[0] for r in conn.execute(text('SELECT league_id FROM leagues')).fetchall()}
    team_ids = {r[0] for r in conn.execute(text('SELECT team_id FROM teams')).fetchall()}
    player_ids = {r[0] for r in conn.execute(text('SELECT player_id FROM players')).fetchall()}
    season_map = {(int(r[0]), str(r[1])): int(r[2]) for r in conn.execute(text('SELECT league_id, season, id FROM league_seasons')).fetchall()}
    venue_rows = conn.execute(text('SELECT name, city FROM venues')).fetchall()
    venue_keys = {
        (re.sub(r'\s+', ' ', (v[0] or '').strip()).lower(), re.sub(r'\s+', ' ', (v[1] or '').strip()).lower())
        for v in venue_rows
    }
    match_rows = conn.execute(text('SELECT fixture_id, league_id, season, home_team_id, away_team_id, venue_name, venue_city FROM matches ORDER BY fixture_id')).fetchall()
    events = conn.execute(text('SELECT id, player_id, assist_id FROM match_events ORDER BY id')).fetchall()
    print('MATCH_COUNT', len(match_rows))
    print('LEAGUE_VALID', sum(1 for r in match_rows if r[1] in league_ids))
    print('SEASON_VALID', sum(1 for r in match_rows if (int(r[1]), str(r[2])) in season_map))
    print('HOME_VALID', sum(1 for r in match_rows if r[3] in team_ids))
    print('AWAY_VALID', sum(1 for r in match_rows if r[4] in team_ids))
    venue_hc = 0
    unresolved = 0
    for r in match_rows:
        name = re.sub(r'\s+', ' ', (r[5] or '').strip()).lower()
        city = re.sub(r'\s+', ' ', (r[6] or '').strip()).lower()
        key = (name, city)
        if name and city and key in venue_keys:
            venue_hc += 1
        else:
            unresolved += 1
    print('VENUE_HC', venue_hc)
    print('VENUE_UNRESOLVED', unresolved)
    print('EVENT_TOTAL', len(events))
    print('PLAYER_VALID', sum(1 for e in events if e[1] is not None and e[1] in player_ids))
    print('PLAYER_NULL', sum(1 for e in events if e[1] is None))
    print('ASSIST_VALID', sum(1 for e in events if e[2] is not None and e[2] in player_ids))
    print('ASSIST_NULL', sum(1 for e in events if e[2] is None))
    print('LINEUP_COUNT', conn.execute(text('SELECT COUNT(*) FROM match_lineups')).scalar())
    print('STAT_COUNT', conn.execute(text('SELECT COUNT(*) FROM match_statistics')).scalar())
    print('H2H_COUNT', conn.execute(text('SELECT COUNT(*) FROM match_h2h')).scalar())
    print('ODDS_COUNT', conn.execute(text('SELECT COUNT(*) FROM odds')).scalar())
    print('MATCH_REFEREE_SOURCE_COUNT', conn.execute(text("SELECT COUNT(*) FROM information_schema.columns WHERE table_name='matches' AND column_name ILIKE '%ref%' ")).scalar())
    print('MATCH_REFEREE_COLUMN_NAMES', [r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='matches' AND column_name ILIKE '%ref%' ORDER BY ordinal_position")).fetchall()])
