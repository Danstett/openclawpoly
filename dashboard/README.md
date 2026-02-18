# FastLoop Dashboard

Simple dashboard to monitor your Polymarket FastLoop trading bot.

## Features

- Active positions with P&L
- Recent trades history
- Auto-refresh every 60 seconds

## Deploy to Vercel

1. Push this repo to GitHub (if not already)

2. Go to [vercel.com](https://vercel.com) and import your repo

3. Set the **Root Directory** to `dashboard`

4. Add environment variable:
   - `POLYMARKET_WALLET_ADDRESS` = your Polygon wallet address

5. Deploy!

## Local Development

```bash
cd dashboard
npm install

# Create .env.local with your wallet address
echo "POLYMARKET_WALLET_ADDRESS=0xYourAddress" > .env.local

npm run dev
```

Open [http://localhost:3000](http://localhost:3000)
