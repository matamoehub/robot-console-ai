#!/bin/sh
set -eu

APP_DIR="${APP_DIR:-/opt/robot/robot-console-ai}"
APP_USER="${APP_USER:-matamoe}"

echo "[1/8] Installing base packages"
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip curl dkms

echo "[2/8] Creating directories"
sudo mkdir -p /opt/robot /opt/robot/bin /opt/robot/etc /opt/robot/logs
sudo chown -R "$APP_USER:$APP_USER" /opt/robot

echo "[3/8] Generating SSH key if needed"
if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
  mkdir -p "$HOME/.ssh"
  ssh-keygen -t ed25519 -C "robot-console-ai" -f "$HOME/.ssh/id_ed25519" -N ""
fi
echo "Public key:"
cat "$HOME/.ssh/id_ed25519.pub"

echo "[4/8] Creating virtualenv"
cd "$APP_DIR"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "[5/8] Installing systemd units"
sudo cp deploy/systemd/robot-console-ai.service /etc/systemd/system/
sudo cp deploy/systemd/hailo-ollama.service /etc/systemd/system/
sudo cp deploy/systemd/vlm-service.service /etc/systemd/system/

echo "[6/8] Optional AI HAT+ 2 packages"
echo "Run these separately when ready:"
echo "  sudo apt install hailo-h10-all"
echo "  sudo dpkg -i hailo_gen_ai_model_zoo_5.1.1_arm64.deb"

echo "[7/8] Creating helper scripts"
cat > /tmp/robot-console-ai-update <<'EOS'
#!/bin/sh
set -eu
cd /opt/robot/robot-console-ai
git fetch origin
git checkout main
git pull --ff-only origin main
. .venv/bin/activate
pip install -r requirements.txt
echo "update ok: $(git rev-parse --short HEAD)"
EOS
sudo mv /tmp/robot-console-ai-update /opt/robot/bin/robot-console-ai-update
sudo chmod 755 /opt/robot/bin/robot-console-ai-update

cat > /tmp/robot-console-ai-restart <<'EOS'
#!/bin/sh
set -eu
exec systemctl restart robot-console-ai
EOS
sudo mv /tmp/robot-console-ai-restart /opt/robot/bin/robot-console-ai-restart
sudo chmod 755 /opt/robot/bin/robot-console-ai-restart

echo "[8/8] Reloading systemd"
sudo systemctl daemon-reload
echo "Next:"
echo "  sudo systemctl enable robot-console-ai"
echo "  sudo systemctl enable hailo-ollama"
echo "  sudo systemctl enable vlm-service"
echo "  sudo systemctl restart robot-console-ai"
echo "  sudo systemctl restart hailo-ollama"
echo "  sudo systemctl restart vlm-service"
