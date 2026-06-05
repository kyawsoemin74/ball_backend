# League Metadata Guide

## 1. SQL seed examples

```sql
UPDATE leagues
SET is_featured = true,
    display_order = 1
WHERE name IN ('Premier League', 'UEFA Champions League', 'La Liga');
```

```sql
INSERT INTO leagues (league_id, name, country, logo, season, is_featured, display_order)
VALUES
  (39, 'Premier League', 'England', NULL, '2024', true, 1),
  (2, 'UEFA Champions League', 'Europe', NULL, '2024', true, 2),
  (140, 'La Liga', 'Spain', NULL, '2024', true, 3)
ON CONFLICT (league_id) DO UPDATE
SET is_featured = EXCLUDED.is_featured,
    display_order = EXCLUDED.display_order;
```

## 2. Admin update example

Use the same metadata fields through any admin form or direct SQL:

```sql
UPDATE leagues
SET is_featured = true,
    display_order = 10
WHERE league_id = 39;
```

## 3. Bulk update example

```sql
UPDATE leagues
SET is_featured = CASE
  WHEN name IN ('Premier League', 'UEFA Champions League', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1') THEN true
  ELSE false
END,
display_order = CASE
  WHEN name = 'Premier League' THEN 1
  WHEN name = 'UEFA Champions League' THEN 2
  WHEN name = 'La Liga' THEN 3
  WHEN name = 'Serie A' THEN 4
  WHEN name = 'Bundesliga' THEN 5
  WHEN name = 'Ligue 1' THEN 6
  ELSE 999
END;
```

This keeps league prioritization in the database instead of hardcoding IDs in business logic.
