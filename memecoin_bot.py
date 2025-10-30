import os, asyncio, httpx, math
from datetime import datetime, timezone
from dateutil.parser import isoparse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ===========================
# CONFIGURATION
# ===========================
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
PULSECHAIN_SLUG = "pulsechain"

MIN_LIQ_USD = 3000         # minimum liquidity
MIN_1H_TXNS = 20           # minimum hourly transactions
MAX_FDV_USD = 20_000_000   # max fully diluted value
MAX_POOL_AGE_HOURS = 48    # show pools newer than 48 hours
HTTP_TIMEOUT = 15

# GeckoTerminal public API
GT_TRENDING = f"https://api.geckoterminal.com/api/v2/networks/{PULSECHAIN_SLUG}/trending_pools"
GT_NEW = f"https://api.geckoterminal.com/api/v2/networks/{PULSECHAIN_SLUG}/new_pools"
DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search"

# ===========================
# HELPER FUNCTIONS
# ===========================
def usd(x):
    try:
        return float(x) if x else 0.0
    except:
        return 0.0

def hours_since(dt_iso):
    try:
        dt = isoparse(dt_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:
        return math.inf

async def fetch_json(client, url, params=None):
    r = await client.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()

def summarize_pool(p):
    attrs = p.get("attributes", {})
    fdv = usd(attrs.get("fdv_usd"))
    liq = usd(attrs.get("reserve_in_usd"))
    price = usd(attrs.get("price_in_usd"))
    buys = attrs.get("buys_1h") or 0
    sells = attrs.get("sells_1h") or 0
    txs = buys + sells
    age_h = hours_since(attrs.get("pool_created_at"))
    base = attrs.get("base_token_symbol")
    quote = attrs.get("quote_token_symbol")
    url = attrs.get("url")
    name = attrs.get("base_token_name") or base

    summary = (
        f"• {name} ({base}) / {quote}\n"
        f"  Price: ${price:,.8f}\n"
        f"  Liquidity: ${liq:,.0f} | FDV: ${fdv:,.0f}\n"
        f"  1h: {buys} buys / {sells} sells (tx={txs})\n"
        f"  Age: {age_h:.1f}h\n"
        f"  Chart: {url}"
    )
    return summary, {"fdv": fdv, "liq": liq, "txs": txs, "age": age_h}

def looks_like_memecoin(m):
    return (
        m["liq"] >= MIN_LIQ_USD and
        m["txs"] >= MIN_1H_TXNS and
        m["fdv"] <= MAX_FDV_USD and
        m["age"] <= MAX_POOL_AGE_HOURS
    )

async def scan_geckoterminal(kind="trending"):
    url = GT_TRENDING if kind == "trending" else GT_NEW
    async with httpx.AsyncClient() as client:
        data = await fetch_json(client, url, params={"include": "base_token,quote_token"})
    pools = data.get("data", [])
    results = []
    for item in pools:
        text, m = summarize_pool(item)
        if looks_like_memecoin(m):
            results.append((m, text))
    results.sort(key=lambda x: (x[0]["txs"], x[0]["liq"]), reverse=True)
    return [t for _, t in results][:10] or ["(no candidates found)"]

# ===========================
# TELEGRAM COMMANDS
# ===========================
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to the PulseChain Memecoin Finder Bot!\n\n"
        "Use these commands:\n"
        "/trending – Top trending PulseChain meme-like tokens\n"
        "/new – Newest PulseChain pools\n"
        "/search <name> – Search for a token (e.g. /search doge)\n\n"
        "⚠️ This bot only provides public data. Always DYOR!"
    )

async def trending(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning trending PulseChain pools...")
    try:
        picks = await scan_geckoterminal("trending")
        await update.message.reply_text("\n\n".join(picks))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

async def new(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning new PulseChain pools...")
    try:
        picks = await scan_geckoterminal("new")
        await update.message.reply_text("\n\n".join(picks))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

async def search(update: Update, _: ContextTypes.DEFAULT_TYPE):
    msg = (update.message.text or "").split(maxsplit=1)
    if len(msg) < 2:
        await update.message.reply_text("Usage: /search <token name>")
        return
    term = msg[1]
    await update.message.reply_text(f"Searching for '{term}' on PulseChain...")
    try:
        async with httpx.AsyncClient() as client:
            js = await fetch_json(client, DEX_SEARCH, params={"q": term})
        pairs = js.get("pairs", [])
        lines = []
        for p in pairs:
            if p.get("chainId") not in ("pulsechain", "pulse"):
                continue
            base = p["baseToken"]["symbol"]
            quote = p["quoteToken"]["symbol"]
            url = p.get("url")
            liq = usd(p.get("liquidity", {}).get("usd"))
            tx = (p.get("txns", {}).get("h1", {}).get("buys", 0)
                  + p.get("txns", {}).get("h1", {}).get("sells", 0))
            price = p.get("priceUsd")
            lines.append(f"• {base}/{quote}  ${float(price):,.8f}  Liq ${liq:,.0f}  tx/hr {tx}\n  {url}")
        if not lines:
            await update.message.reply_text("(no PulseChain results found)")
        else:
            await update.message.reply_text("\n\n".join(lines[:10]))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

# ===========================
# MAIN APP
# ===========================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("trending", trending))
    app.add_handler(CommandHandler("new", new))
    app.add_handler(CommandHandler("search", search))
    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
