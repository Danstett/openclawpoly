import { NextResponse } from 'next/server'

const DATA_API = 'https://data-api.polymarket.com'

export async function GET() {
  const walletAddress = process.env.POLYMARKET_WALLET_ADDRESS

  if (!walletAddress) {
    return NextResponse.json({ error: 'POLYMARKET_WALLET_ADDRESS not configured' }, { status: 500 })
  }

  try {
    const res = await fetch(
      `${DATA_API}/positions?user=${walletAddress}&sizeThreshold=0.1&limit=500`,
      { cache: 'no-store' }
    )

    if (!res.ok) {
      return NextResponse.json({ error: `Data API error: ${res.status}` }, { status: res.status })
    }

    const rawPositions = await res.json()
    const positionsList = Array.isArray(rawPositions) ? rawPositions : []

    // Normalize Data API format to match dashboard frontend expectations
    const positions = positionsList.map((p: any) => {
      const outcome = (p.outcome || '').toLowerCase()
      const size = parseFloat(p.size || '0')
      return {
        question: p.title || '',
        slug: p.eventSlug || p.slug || '',
        condition_id: p.conditionId || '',
        market_id: p.conditionId || '',
        side: outcome,
        shares: size,
        shares_yes: outcome === 'yes' ? size : 0,
        shares_no: outcome === 'no' ? size : 0,
        avg_cost: parseFloat(p.avgPrice || '0'),
        cost_basis: parseFloat(p.initialValue || '0'),
        current_value: parseFloat(p.currentValue || '0'),
        pnl: parseFloat(p.cashPnl || '0'),
        resolves_at: p.endDate || '',
        redeemable: p.redeemable || false,
        redeemable_side: p.redeemable ? outcome : null,
      }
    })

    const totalValue = positions.reduce((sum: number, p: any) => sum + (p.current_value || 0), 0)
    const totalPnl = positions.reduce((sum: number, p: any) => sum + (p.pnl || 0), 0)

    return NextResponse.json({
      positions,
      total_value: totalValue,
      polymarket_pnl: totalPnl,
    })
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 })
  }
}
