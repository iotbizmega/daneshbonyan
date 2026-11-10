#!/usr/bin/env bash
set -e
command -v curl >/dev/null || { echo "curl لازم است."; exit 1; }
command -v python3 >/dev/null || { echo "python3 لازم است."; exit 1; }

if [ -f .env ]; then
  read -rp ".env از قبل وجود دارد. بازنویسی شود؟ [y/N]: " AGAIN
  [ "$AGAIN" = "y" ] || { echo "دست نکردیم."; exit 0; }
fi

# آخرین چت‌هایی که ربات پیام در آن‌ها دارد: «شناسه<TAB>عنوان<TAB>نوع»
chats() {
  curl -s --max-time 25 "$1/getUpdates?timeout=5" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not d.get("ok"):
    print("! " + str(d.get("description", ""))); sys.exit(0)
found = {}
for u in d.get("result", []):
    for k in ("message", "channel_post", "edited_channel_post", "my_chat_member"):
        c = (u.get(k) or {}).get("chat")
        if c:
            found[c["id"]] = (c.get("title") or c.get("username") or "?", c.get("type", ""))
for cid, (t, ty) in sorted(found.items()):
    print("%s\t%s\t%s" % (cid, t, ty))
'
}

pick() {  # $1 = chat list text ; خروج: شناسه‌ی انتخاب‌شده
  rows=$(printf '%s\n' "$1" | grep $'\t' || true)
  n=$(printf '%s\n' "$rows" | grep -c . || true)
  [ "$n" = "0" ] && return 1
  if [ "$n" = "1" ]; then printf '%s' "$rows" | cut -f1; return 0; fi
  printf '%s\n' "$rows" | awk -F'\t' '{printf "  %d) %s (%s)\n", NR, $2, $3}' >&2
  read -rp "شماره‌ی مقصد: " I
  printf '%s\n' "$rows" | sed -n "${I}p" | cut -f1
}

echo "== ربات تلگرام =="
read -rp "توکن ربات تلگرام (از @BotFather): " TOKEN
ME=$(curl -s --max-time 25 "https://api.telegram.org/bot$TOKEN/getMe" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print(d["result"]["username"] if d.get("ok") else "")' 2>/dev/null || true)
[ -z "$ME" ] && { echo "توکن درست نیست (getMe خطا داد)."; exit 1; }
echo "ربات: @$ME"
echo "حالا به این ربات یک پیام بدهید (یا او را در گروه/کانال موردنظر اضافه و ادمین کنید) و Enter بزنید..."
read -r
TL=$(chats "https://api.telegram.org/bot$TOKEN" || true)
[ -z "$TL" ] && { echo "پیامی پیدا نشد. توکن یا پیام را چک کنید."; exit 1; }
CHAT=$(pick "$TL") || { echo "چتی پیدا نشد."; exit 1; }
echo "مقصد: $CHAT"

echo
echo "== فیلتر هوشمند (اختیاری) =="
echo "با هر سرویس سازگار با OpenAI کار می‌کند. خالی = بدون فیلتر هوشمند."
echo "  Gemini  base: https://generativelanguage.googleapis.com/v1beta/openai   model: gemini-2.5-flash"
echo "  Groq    base: https://api.groq.com/openai/v1                            model: llama-3.1-8b-instant"
read -rp "LLM_BASE_URL: " URL
KEY=""; MODEL=""
if [ -n "$URL" ]; then
  read -rp "LLM_API_KEY: " KEY
  read -rp "LLM_MODEL (از فهرست مدل‌های خود سرویس): " MODEL
fi

echo
echo "== ارسال همزمان به بله (اختیاری) =="
echo "ربات بله را از @Botfather در پیام‌رسان بله می‌سازید. API بله: https://tapi.bale.ai"
read -rp "BALE_TOKEN: " BT
BC=""
if [ -n "$BT" ]; then
  BM=$(curl -s --max-time 25 "https://tapi.bale.ai/bot$BT/getMe" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print(d["result"]["username"] if d.get("ok") else "")' 2>/dev/null || true)
  if [ -z "$BM" ]; then
    echo "توکن بله کار نکرد؛ بدون ارسال به بله ادامه می‌دهیم."
    BT=""
  else
    echo "ربات بله: @$BM — به آن یک پیام بدهید و Enter بزنید..."
    read -r
    BL=$(chats "https://tapi.bale.ai/bot$BT" || true)
    BC=$(pick "$BL" || true)
    [ -z "$BC" ] && read -rp "CHAT_ID بله به صورت دستی: " BC
  fi
fi

INTERVAL=300
MAXCH=150
read -rp "فاصله‌ی بررسی بر ثانیه [$INTERVAL]: " I; INTERVAL=${I:-$INTERVAL}
read -rp "سقف کانال‌های پیگیری‌شده [$MAXCH]: " M; MAXCH=${M:-$MAXCH}

cat > .env <<ENV
TELEGRAM_TOKEN=$TOKEN
CHAT_ID=$CHAT
BALE_TOKEN=$BT
BALE_CHAT_ID=$BC
INTERVAL_SECONDS=$INTERVAL
MAX_CHANNELS=$MAXCH
LLM_BASE_URL=$URL
LLM_API_KEY=$KEY
LLM_MODEL=$MODEL
ENV
chmod 600 .env
curl -s --max-time 20 "https://api.telegram.org/bot$TOKEN/sendMessage" \
  -d chat_id="$CHAT" -d text="✅ تنظیم انجام شد؛ ایجنت آماده‌ی اجرای اول است." >/dev/null || true
echo
echo "تمام. حالا:"
echo "    docker compose up -d --build"
echo "    docker compose logs -f"
