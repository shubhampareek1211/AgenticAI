These unmodified men's international JSON match files and the Register CSV
subsets for matches 1022353 and 1041615 are from [Cricsheet](https://cricsheet.org/), downloaded on
2026-09-28 UTC. `manifest.json` records the source archives, SHA-256 hashes, match
dates, format versions, and revisions. Match files 1022353 (ODI) and 1041615
(T20I) include Virat Kohli, Rohit Sharma, and Jasprit Bumrah.

Match 1229824 is a real data-quality regression: its registry assigns J Butler's
same stable ID to both teams. Its innings must be retained as ambiguous and
excluded from aggregates without guessing a corrected identity.

Sources: [men's ODI JSON](https://cricsheet.org/downloads/odis_male_json.zip),
[men's T20I JSON](https://cricsheet.org/downloads/t20s_male_json.zip),
[Register](https://cricsheet.org/register/).

Attribution: Cricsheet. Register data is licensed under the
[Open Data Commons Attribution License](https://opendatacommons.org/licenses/by/1.0/).
Retain this attribution when using or redistributing these fixtures. These are
available-match samples, not official complete career records. Synthetic edge
cases in `tests/helpers.py` are separate from these source fixtures.
