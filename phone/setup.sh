#!/data/data/com.termux/files/usr/bin/bash
# One-time setup of the fantasy tracker on an Android phone, inside the Termux app.
#
#   curl -fsSL https://raw.githubusercontent.com/ViliusJaz/fantasy-tracker/main/phone/setup.sh | bash
#
# Installs Python, git and the GitHub CLI, logs in to GitHub (in the browser), copies the
# repository to the phone and adds an "Update fantasy site" button for the Termux:Widget
# home-screen widget. The button runs the same publish.sh as the Mac: it fetches BasketNews
# over the phone's connection (about 1.5 MB) and uploads the new site (a few MB at most).
set -e

echo "== Installing Python, git and GitHub CLI"
yes | pkg update >/dev/null
pkg install -y python git gh

echo "== GitHub login"
if ! gh auth status >/dev/null 2>&1; then
  echo "A one-time code will be shown: open https://github.com/login/device and enter it."
  gh auth login --hostname github.com --git-protocol https --web
fi
gh auth setup-git

echo "== Copying the tracker to the phone"
if [ ! -d ~/fantasy-tracker/.git ]; then
  git clone -q https://github.com/ViliusJaz/fantasy-tracker.git ~/fantasy-tracker
fi
cd ~/fantasy-tracker
git config user.name "$(gh api user --jq .login)"
git config user.email "$(gh api user --jq '"\(.id)+\(.login)@users.noreply.github.com"')"

echo "== Home-screen button"
mkdir -p ~/.shortcuts
cat > ~/.shortcuts/"Update fantasy site" <<'EOS'
#!/data/data/com.termux/files/usr/bin/bash
cd ~/fantasy-tracker && bash publish.sh
echo
read -r -p "Done. Press Enter to close. " _
EOS
chmod +x ~/.shortcuts/"Update fantasy site"

echo
echo "All set. Add the Termux:Widget widget to your home screen and tap \"Update fantasy site\"."
echo "You can also run it here any time with:  bash ~/fantasy-tracker/publish.sh"
