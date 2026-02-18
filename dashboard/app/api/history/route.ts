import { NextResponse } from 'next/server'

const DATA_API = 'https://data-api.polymarket.com'

export async function GET() {
  const walletAddress = process.env.POLYMARKET_WALLET_ADDRESS

  if (!walletAddress) {
    return NextResponse.json({ error: 'POLYMARKET_WALLET_ADDRESS not configured' }, { status: 500 })
  }

  try {
    const res = await fetch(
      `${DATA_API}/activity?user=${walletAddress}&limit=50`,
      { cache: 'no-store' }
    )

    if (!res.ok) {
      return NextResponse.json({ trades: [], error: `Data API error: ${res.status}` }, { status: 200 })
    }

    const rawActivity = await res.json()
    const activityList = Array.isArray(rawActivity) ? rawActivity : []

    // Normalize to match dashboard Trade interface
    const trades = activityList.map((a: any) => ({
      id: a.id || '',
      market_question: a.title || '',
      side: (a.outcome || '').toLowerCase(),
      action: (a.type || 'TRADE').toLowerCase() === 'sell' ? 'sell' : 'buy',
      shares: parseFloat(a.size || '0'),
      cost: parseFloat(a.amount || a.cashAmount || '0'),
      price_before: parseFloat(a.price || '0'),
      created_at: a.timestamp || a.createdAt || '',
      source: 'polymarket-clob',
    }))

    return NextResponse.json({ trades })
  } catch (error) {
    return NextResponse.json({ trades: [], error: String(error) }, { status: 200 })
  }
}
