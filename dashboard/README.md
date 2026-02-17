# FastLoop Dashboard

Simple dashboard to monitor your Polymarket FastLoop trading bot.

## Features

- Wallet balance display
- Active positions with P&L
- Recent trades history
- Auto-refresh every 30 seconds

## Deploy to Vercel

1. Push this repo to GitHub (if not already)

2. Go to [vercel.com](https://vercel.com) and import your repo

3. Set the **Root Directory** to `dashboard`

4. Add environment variable:
   - `SIMMER_API_KEY` = your Simmer API key

5. Deploy!

## Local Development

```bash
cd dashboard
npm install

# Create .env.local with your API key
echo "SIMMER_API_KEY=your_key_here" > .env.local

npm run dev
```

Open [http://localhost:3000](http://localhost:3000)
