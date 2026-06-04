-- ALTER TABLE statements to add team_name and team_logo to standings
ALTER TABLE public.standings
    ADD COLUMN team_name VARCHAR(255);

ALTER TABLE public.standings
    ADD COLUMN team_logo VARCHAR(1024);

-- Optional: add indexes if queries will filter/sort by team_name
-- CREATE INDEX ix_standings_team_name ON public.standings (team_name);
