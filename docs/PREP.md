# Prep tab: preparing for the next opponent

The Prep tab is a chat next to the board. You tell it who you play in a team
league on chess-results.com, and it works through the preparation one step at
a time: who will probably sit at your board, what that player opens with, where
they go wrong, and which lines fit both your repertoire and their weak spots.
Every move sequence in an answer is a link; clicking it shows the line on the
board.

Open it with **Ctrl+P** (switch between Analysis and Prep), **Ctrl+J** (go
straight to a new prep), or **Library → Prep: next opponent**.

## What you need

- **Claude Code**, the `claude` command-line tool, installed and on `PATH`.
  If ChessLab cannot find it, the message box is disabled and the status line
  says so.
- **You are logged in to Claude Code.** Run `claude` once in a terminal and
  sign in. ChessLab does not ask for an API key and stores none.
- **Stockfish**, the same engine the rest of ChessLab uses. Only step 3 needs it.
- **Your own games in ChessLab** (Lichess, Chess.com or over-the-board). Only
  step 4 needs them, to suggest lines you actually play.

The Windows build (`ChessLab.exe`) has no Prep tools: it is a frozen bundle
without a Python interpreter to run `python -m chesslab.prep`. Run ChessLab
from source to use Prep.

## How Claude is connected

ChessLab does not talk to the Anthropic API itself. For every message you send
it starts the Claude Code CLI as a child process, the same `claude` you would
run in a terminal:

```
claude -p --model sonnet --output-format stream-json --verbose
       --setting-sources project --disable-slash-commands --strict-mcp-config
       --append-system-prompt <prep instructions>
       --session-id <id>        (first message; --resume <id> afterwards)
       --tools Bash WebFetch Read
       --allowedTools "Bash(<python> -m chesslab.prep:*)"
                      "WebFetch(domain:chess-results.com)"
                      "WebFetch(domain:s2.chess-results.com)"
                      "WebFetch(domain:ratings.fide.com)"
                      Read
```

What each part is for:

- **Authentication.** Claude Code uses whatever account it is logged into: a
  Claude subscription (Pro or Max) or an `ANTHROPIC_API_KEY` in the
  environment, if you set one. ChessLab passes its environment through
  unchanged, so the choice is yours. Usage counts against that account.
- **`--model sonnet`.** Each step is a few tool runs and a short summary,
  which Sonnet handles well and keeps usage low.
- **`--setting-sources project`, `--strict-mcp-config`, `--disable-slash-commands`.**
  The run starts in the prep's own folder, which has no project settings, so
  your personal hooks, plugins, MCP servers and slash commands do not load.
  Without this a hook that pings your phone would fire on every prep message.
- **`--tools` and `--allowedTools`.** Claude can only read files, fetch pages
  from chess-results.com and ratings.fide.com, and run the prep commands listed
  below. Any other shell command is refused, so it cannot touch files outside
  what those commands write.
- **`--session-id` / `--resume`.** Each prep has a fixed session id, stored in
  the database. The first message creates the session and later messages
  continue it, so Claude remembers the earlier steps. If the two disagree (a
  first run that died half-way, a session removed by hand), ChessLab retries
  once with the other flag.
- **stream-json.** Claude Code prints one JSON event per line. Tool calls turn
  into the status line under the chat ("games Muller, Tomas", "reading
  ratings.fide.com"), and the final `result` event becomes the answer.

The message itself goes in through stdin, not as an argument, because
`--tools` and `--allowedTools` accept any number of values and would swallow it.

The instructions Claude gets are in `chesslab/prep/prompt.py`. Besides the
steps they set the writing rules: answer in your language, keep it short,
write every line as a `line:` link, and never invent games, ratings or results
that the tools did not return.

### Privacy

Your messages, and what the prep commands print, go to Anthropic through Claude
Code. That output includes public data from chess-results and FIDE and, at step
4, a summary of your own opening repertoire (the first moves of your games and
your score with them). Nothing else from your database is sent. Pages are
fetched from chess-results.com and ratings.fide.com. The rest of ChessLab works
without Claude.

## The steps

Claude stops after each step and asks whether to go on, so you control how far
it goes and how much it costs.

1. **Who you will play.** Write the opposing team, your board number and the
   tournament link, for example
   `Humenné C, board 7, https://chess-results.com/tnr1464725.aspx`.
   Claude runs `lineup`: does the team always put the same player on that
   board, or rotate? It names the likely opponents with how often each played
   there, and runs `card` for each. The cards appear above the chat: FIDE ID,
   year of birth, federation, title, ratings, the last 12 months of rating
   changes, and their results in this tournament.

   To prepare for one particular player, name them directly instead: a name,
   a FIDE ID, or a chess-results or FIDE link. Claude skips the line-up and
   starts from the card.

2. **Openings.** Claude downloads the player's games from the chess-results
   game database with `games`, games from this tournament first, and says what
   they play as White and as Black, how often, and how it went.

3. **Mistakes and style** (when you ask). `analyse` runs Stockfish over their
   recent games and lists the moves that lost the most: in which phase, what
   kind of mistake, whether they fall apart late in the game. Each mistake
   comes with a link to the position.

4. **Your prep** (when you ask). `mine` summarises your own repertoire from the
   ChessLab database, and Claude proposes concrete lines that fit what you
   play and steer towards where this opponent tends to go wrong.

**Games…** under the chat opens any PGN the prep downloaded on the board, so
you can play through their games yourself.

## Saved preps

Every prep is saved. The list at the top of the tab holds them by the first
message's text; **New prep** starts another one. A prep keeps:

- a row in the `preps` table of `games.sqlite3`: title, creation time, Claude
  session id and the chat transcript;
- a folder, `~/.local/share/chesslab/prep/<id>/` (`%APPDATA%\ChessLab\prep\<id>\`
  on Windows), with the downloaded PGN files and `cards.json`, the player cards
  shown above the chat.

Deleting the folder removes the downloaded games and cards; the chat stays in
the database.

## The commands

Claude uses these, and you can run them yourself from the repository root.
They print plain text, so the same output Claude reads is what you see.

```
python -m chesslab.prep lineup "<tournament link>" "<team or part of its name>"
python -m chesslab.prep card <FIDE_ID> --link "<tournament link>"
python -m chesslab.prep card --name "Last, First" --link "<tournament link>"
python -m chesslab.prep games "Last, First:FIDE_ID" [more players] --out . [--event "2. liga"]
python -m chesslab.prep analyse "<Last_First.pgn>" "Last, First"
python -m chesslab.prep mine
```

- **`lineup`** reads every played round of the tournament and shows, board by
  board, who the team fielded and how each game ended. The team name matches
  when every word you give starts a word of the team's name, so `Humenné C`
  finds `Humenné Reinter C`.
- **`card`** combines the player's FIDE profile with their part of the
  tournament and writes it to `cards.json` in the current folder. Without a
  FIDE ID it looks the player up by name in the tournament's starting list.
- **`games`** downloads the player's games from the chess-results PGN search,
  newest first, and writes `Last_First.pgn`. With `:FIDE_ID` the search is by
  ID, which avoids namesakes. `--event` keeps only games whose event contains
  that text.
- **`analyse`** checks the player's last 10 games at 0.08 s per move and lists
  the moves that dropped their winning chances by 15% or more, sorted by phase
  (opening before move 15, endgame once little material is left). A typical
  run takes about a minute.
- **`mine`** prints the first six plies of your games as White and as Black,
  all games and over-the-board separately, with your score, plus the moves you
  starred in the repertoire table.

## Limits

- chess-results pages are scraped, not read through an API. If the site
  changes its layout, `lineup` and `card` will need fixing.
- The game database on chess-results holds only games someone entered. Many
  club and league games are missing, so a short list of games is normal.
- Players are matched between chess-results pages by name, because the site
  numbers players differently on each page. Two players with the same name in
  one tournament can be mixed up.
- `analyse` is quick, not deep. It finds clear mistakes, not subtle ones.
- Each message costs a Claude Code run. Steps 2 to 4 run several commands and
  can take a minute or two.
