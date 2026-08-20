# Setting this up

Four steps. The only fiddly one is the token.

## 1. Make the repository

A profile README lives in a repository named exactly after your account, so
this one has to be called `FrancisMarzynski`. GitHub then shows its README at
the top of your profile page.

```bash
cd ~/Downloads/FrancisMarzynski
git init -b main
git add -A
git commit -m "Profile card"
gh repo create FrancisMarzynski --public --source=. --push
```

## 2. Make a token

The card counts private repositories, and the Action needs permission to ask
about them. A default `GITHUB_TOKEN` cannot see across repositories, so it has
to be a personal access token.

1. Go to <https://github.com/settings/tokens?type=beta> and choose
   **Generate new token** (classic works too; the scopes below are the classic
   names).
2. Tick **`repo`** and **`read:user`**.
   - `repo` is what lets it count private repositories.
   - `read:user` is what makes the commit total work. Without it GitHub
     silently returns zero commits and the script falls back to counting only
     your own repositories.
3. Give it a long expiry, or you will be back here in 30 days.
4. Copy the token.

Then hand it to the repository:

```bash
gh secret set ACCESS_TOKEN --repo FrancisMarzynski/FrancisMarzynski
# paste the token when prompted
```

## 3. Run it once

```bash
gh workflow run "Rebuild profile card" --repo FrancisMarzynski/FrancisMarzynski
gh run watch --repo FrancisMarzynski/FrancisMarzynski
```

After that it rebuilds itself at 04:00 UTC every day.

## 4. Check your profile

<https://github.com/FrancisMarzynski>

---

# Changing things

## The words

Everything static lives in the `STATIC` list at the top of `today.py`. Add a
line, delete a line, rename a section - it is a plain list and the layout
measures itself afterwards.

```bash
python3 today.py --offline     # redraw from cached stats, no network
open dark_mode.svg             # look at it
```

## The portrait

```bash
python3 make_ascii.py --preview                          # try settings
python3 make_ascii.py --box 120,50,240,260 --gamma 0.6   # tune the crop
python3 make_ascii.py                                    # save it
python3 today.py --offline                               # redraw the cards
```

The single biggest improvement is a better source photo. Drop a tight,
high-contrast head-and-shoulders crop in as `photo.png` and re-run. A wide
shot with a bright background behind a dark subject - which is what the
current avatar is - is the hardest case there is.

If the photo is a JPEG, convert it first:

```bash
sips -s format png yourphoto.jpg --out photo.png
```

## The line count

`Lines of code` is the least trustworthy number on the card. A single
committed lockfile or vendored dependency adds six figures without a line
being written by hand. Three knobs at the top of `today.py`:

- `SHOW_LINES_OF_CODE = False` removes the line entirely.
- `MAX_LINES_PER_COMMIT` (default 1000) drops repositories whose commits
  average more than that many changed lines. Each run prints what it skipped.
- `EXCLUDE_REPOS` drops named repositories by hand.
