#!/bin/bash
# =====================================================
# AMILCAR Auto Care — Fly.io Deploy Script
# شغّل هذا الملف بعد تغيير الإنترنت:  bash deploy.sh
# =====================================================
set -e
cd /home/raaft/amilcar

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AMILCAR — Fly.io Deployment"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Install flyctl ────────────────
if ! command -v flyctl &>/dev/null && ! [ -f "$HOME/.fly/bin/flyctl" ]; then
  echo "▶ Installing flyctl..."
  curl -L https://fly.io/install.sh | sh
fi
export PATH="$HOME/.fly/bin:$PATH"
echo "✅ flyctl $(flyctl version --json 2>/dev/null | grep -o '"[0-9.]*"' | head -1) ready"

# ── Step 2: Login ─────────────────────────
echo ""
echo "▶ Login to Fly.io (browser will open)..."
flyctl auth login

# ── Step 3: Create app (first time only) ──
echo ""
if flyctl status --app amilcar-autocare &>/dev/null; then
  echo "✅ App 'amilcar-autocare' already exists"
else
  echo "▶ Creating app..."
  flyctl apps create amilcar-autocare --org personal
fi

# ── Step 4: Create persistent volume ──────
if flyctl volumes list --app amilcar-autocare 2>/dev/null | grep -q amilcar_data; then
  echo "✅ Volume already exists"
else
  echo "▶ Creating 1GB persistent volume..."
  flyctl volumes create amilcar_data --size 1 --region cdg --app amilcar-autocare
fi

# ── Step 5: Set secrets ───────────────────
echo ""
echo "▶ Setting secure SECRET_KEY..."
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
flyctl secrets set SECRET_KEY="$SECRET" --app amilcar-autocare

# ── Step 6: Deploy ────────────────────────
echo ""
echo "▶ Deploying... (this takes ~3-5 minutes first time)"
flyctl deploy --app amilcar-autocare

# ── Done ─────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 DEPLOYED!"
echo "  🌐  https://amilcar-autocare.fly.dev"
echo "  📅  https://amilcar-autocare.fly.dev/book"
echo "  👤  Login: admin / admin123"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
