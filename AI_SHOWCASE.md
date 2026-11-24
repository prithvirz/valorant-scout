# Building VALORANT Scout with Junie

VALORANT Scout explores whether an Operator player's early first-contact location repeats in a later professional match. I used JetBrains Junie CLI as the coding agent throughout development. ChatGPT helped plan milestones and independently checked the results against the match feeds.

## From raw data to a working demo

I asked Junie to build a Python CLI that streams Cloud9's compressed GRID event feeds, identifies eligible defense rounds, finds early damage contacts and fits a 15-meter area using reference matches. Junie then built a Streamlit dashboard with a map-coordinate plot, evidence table and a reveal for the later match. It also added tests and a downloader that checks the demo feeds against pinned file digests.

## Fixing the analysis

The first implementation produced only three eligible Sentinels rounds, while an independent check found six. The issue was inventory at barrier drop: the parser could miss an Operator acquired during freeze time. I gave Junie the finding and asked it to read the player inventory from the matching game snapshot at the end of freeze time. Junie also corrected first-contact handling so later damage could not overwrite an earlier event. It added regression tests covering both cases.

With those changes, the real-feed result was one matching round out of six eligible Sentinels rounds. Round 9 showed first enemy damage at approximately 17 seconds after barrier drop. The other five rounds had no observed damage within the window. The reference area covered three of twelve eligible rounds in the Evil Geniuses and KRÜ matches. These counts are a small sample, and damage is a proxy for first contact.

## Packaging the demonstration

Junie added an explicitly fictional sample mode for the public hosted app, plus tests for the reveal and round inspection. The public repository documents how to fetch the official feeds and reproduce the real-data result locally. This keeps the demonstration usable while clearly identifying which data is fictional.

The workflow combined Junie's implementation and tests with independent checks of the output. The discrepancy in the first result guided a targeted correction and made the final evidence easier for a coach to interpret.
