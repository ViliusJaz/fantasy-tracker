-- Version 2: the positions a player could play in a round (the optimal-lineup analytics need
-- them to respect formations). The archive files already carry them, so every file is
-- simply loaded again (forgetting what was ingested makes the next refresh re-read data/).

ALTER TABLE player_rounds ADD COLUMN positions TEXT NOT NULL DEFAULT '[]';
DELETE FROM ingested;
