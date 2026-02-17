import { NextResponse } from 'next/server'

const SIMMER_BASE = process.env.SIMMER_API_BASE || 'https://api.simmer.markets'

export async function GET() {
  const apiKey = process.env.SIMMER_API_KEY
  
  if (!apiKey) {
    return NextResponse.json({ error: 'SIMMER_API_KEY not configured' }, { status: 500 })
  }

  try {
    // Try multiple endpoints to get trade history
    const endpoints = [
      '/api/sdk/trades?limit=50',
      '/api/sdk/history?limit=50',
      '/api/sdk/orders?limit=50',
    ]

    let trades: any[] = []
    let lastError = null

    for (const endpoint of endpoints) {
      try {
        const res = await fetch(`${SIMMER_BASE}${endpoint}`, {
          headers: {
            'Authorization': `Bearer ${apiKey}`,
            'User-Agent': 'fastloop-dashboard/1.0',
          },
          cache: 'no-store',
        })

        if (res.ok) {
          const data = await res.json()
          // Extract trades from response
          const tradesList = data.trades || data.orders || data.history || data.data || data
          if (Array.isArray(tradesList) && tradesList.length > 0) {
            trades = tradesList
            break
          }
        }
      } catch (e) {
        lastError = e
      }
    }

    return NextResponse.json({ trades, error: trades.length === 0 ? lastError : null })
  } catch (error) {
    return NextResponse.json({ trades: [], error: String(error) }, { status: 200 })
  }
}
