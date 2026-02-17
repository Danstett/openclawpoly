'use client'

import { useEffect, useState, useCallback } from 'react'

interface Position {
  market_id?: string
  question?: string
  shares_yes?: number
  shares_no?: number
  current_price?: number
  current_probability?: number
  current_value?: number
  cost_basis?: number
  avg_cost?: number
  pnl?: number
  venue?: string
  resolves_at?: string
  redeemable?: boolean
  redeemable_side?: string | null
  sources?: string[]
}

interface Trade {
  id?: string
  market_id?: string
  market_question?: string
  side?: string
  action?: string
  shares?: number
  cost?: number
  price_before?: number
  price_after?: number
  created_at?: string
  venue?: string
  source?: string
  reasoning?: string
}

interface Portfolio {
  balance_usdc?: number
  total_exposure?: number
  positions_count?: number
  pnl_total?: number
  pnl_24h?: number
}

interface PositionsResponse {
  positions?: Position[]
  total_value?: number
  polymarket_pnl?: number
}

interface ApiLog {
  timestamp: string
  endpoint: string
  status: string
}

type PositionStatus = 'WON' | 'LOST' | 'PENDING' | 'REDEEMABLE'

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [positionsData, setPositionsData] = useState<PositionsResponse | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [apiLogs, setApiLogs] = useState<ApiLog[]>([])
  const [showDebug, setShowDebug] = useState(false)
  const [rawData, setRawData] = useState<any>({})

  const addLog = useCallback((endpoint: string, status: string) => {
    setApiLogs(prev => [{
      timestamp: new Date().toLocaleTimeString(),
      endpoint,
      status,
    }, ...prev.slice(0, 19)])
  }, [])

  const fetchData = useCallback(async () => {
    try {
      const [portfolioRes, positionsRes, historyRes] = await Promise.all([
        fetch('/api/portfolio'),
        fetch('/api/positions'),
        fetch('/api/history'),
      ])

      const portfolioJson = await portfolioRes.json()
      const positionsJson = await positionsRes.json()
      const historyJson = await historyRes.json()

      setRawData({ portfolio: portfolioJson, positions: positionsJson, history: historyJson })

      addLog('/api/portfolio', portfolioRes.ok ? 'OK' : 'ERROR')
      addLog('/api/positions', positionsRes.ok ? 'OK' : 'ERROR')
      addLog('/api/history', historyRes.ok ? 'OK' : 'ERROR')

      // Set portfolio (handle error responses)
      if (!portfolioJson?.error) {
        setPortfolio(portfolioJson)
        setError(null)
      } else {
        setError(typeof portfolioJson.error === 'string' ? portfolioJson.error : 'API Error')
      }

      // Set positions
      setPositionsData(positionsJson)

      // Set trades
      const tradesArray = historyJson?.trades || []
      setTrades(Array.isArray(tradesArray) ? tradesArray : [])

      setLastUpdated(new Date())
    } catch (err) {
      setError(String(err))
      addLog('fetch', 'ERROR')
    } finally {
      setLoading(false)
    }
  }, [addLog])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 60000)
    return () => clearInterval(interval)
  }, [fetchData])

  // Get position status
  const getPositionStatus = (pos: Position): PositionStatus => {
    if (pos.redeemable) return 'REDEEMABLE'
    
    const resolveTime = pos.resolves_at ? new Date(pos.resolves_at) : null
    const now = new Date()
    
    if (resolveTime && resolveTime > now) return 'PENDING'
    
    // Market has resolved
    if (pos.current_price === 1) return 'WON'
    if (pos.current_price === 0) return 'LOST'
    if ((pos.pnl || 0) > 0) return 'WON'
    if ((pos.pnl || 0) < 0) return 'LOST'
    
    return 'PENDING'
  }

  // Get position side
  const getPositionSide = (pos: Position): 'YES' | 'NO' => {
    if ((pos.shares_yes || 0) > 0) return 'YES'
    return 'NO'
  }

  // Get position shares
  const getPositionShares = (pos: Position): number => {
    return pos.shares_yes || pos.shares_no || 0
  }

  // Filter for fast market positions
  const positions = positionsData?.positions || []
  const fastPositions = positions.filter(p => 
    (p.question || '').toLowerCase().includes('up or down')
  )

  // Categorize positions
  const redeemablePositions = fastPositions.filter(p => p.redeemable === true)
  const pendingPositions = fastPositions.filter(p => {
    if (p.redeemable) return false
    const resolveTime = p.resolves_at ? new Date(p.resolves_at) : null
    return resolveTime && resolveTime > new Date()
  })
  const resolvedPositions = fastPositions.filter(p => {
    if (p.redeemable) return false
    const resolveTime = p.resolves_at ? new Date(p.resolves_at) : null
    return !resolveTime || resolveTime <= new Date()
  })

  // Calculate redeemable value
  const redeemableValue = redeemablePositions.reduce((sum, p) => sum + (p.current_value || 0), 0)

  // Get totals from API
  const totalValue = positionsData?.total_value || 0
  const totalPnL = portfolio?.pnl_total || positionsData?.polymarket_pnl || 0
  const balance = portfolio?.balance_usdc || 0

  // Filter trades for fast markets with actual shares
  const fastTrades = trades.filter(t => {
    const q = (t.market_question || '').toLowerCase()
    return q.includes('up or down') && (t.shares || 0) > 0
  })

  // Format helpers
  const formatCurrency = (value: number | undefined | null) => {
    if (value === undefined || value === null || isNaN(value)) return '$0.00'
    const sign = value < 0 ? '-' : ''
    return `${sign}$${Math.abs(value).toFixed(2)}`
  }

  const formatPnL = (value: number | undefined | null) => {
    if (value === undefined || value === null || isNaN(value)) return '$0.00'
    const sign = value >= 0 ? '+' : ''
    return `${sign}$${value.toFixed(2)}`
  }

  const formatShortMarket = (question: string) => {
    // "Bitcoin Up or Down - February 17, 1:35PM-1:40PM ET" -> "Feb 17, 1:35-1:40PM"
    const match = question.match(/(\w+)\s+(\d+),\s*(\d+:\d+[AP]M)-(\d+:\d+[AP]M)/)
    if (match) {
      return `${match[1].slice(0, 3)} ${match[2]}, ${match[3]}-${match[4]}`
    }
    return question.slice(0, 40)
  }

  const formatTradeTime = (dateStr: string | undefined) => {
    if (!dateStr) return ''
    try {
      const date = new Date(dateStr)
      return date.toLocaleTimeString('en-US', { 
        hour: 'numeric', 
        minute: '2-digit',
        hour12: true 
      })
    } catch {
      return ''
    }
  }

  // Status badge colors
  const getStatusBadge = (status: PositionStatus) => {
    switch (status) {
      case 'WON':
        return 'bg-green-900/50 text-green-400 border-green-700'
      case 'LOST':
        return 'bg-red-900/50 text-red-400 border-red-700'
      case 'REDEEMABLE':
        return 'bg-yellow-900/50 text-yellow-400 border-yellow-700'
      case 'PENDING':
      default:
        return 'bg-blue-900/50 text-blue-400 border-blue-700'
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#0a0a0a]">
        <div className="text-xl text-gray-400">Loading dashboard...</div>
      </div>
    )
  }

  return (
    <main className="min-h-screen p-4 md:p-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl md:text-3xl font-bold">FastLoop Dashboard</h1>
        <div className="flex items-center gap-2 text-sm text-gray-500">
          {lastUpdated && <span className="hidden md:inline">Updated: {lastUpdated.toLocaleTimeString()}</span>}
          <button 
            onClick={fetchData}
            className="px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-white transition"
          >
            Refresh
          </button>
          <button 
            onClick={() => setShowDebug(!showDebug)}
            className="px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-white transition"
          >
            {showDebug ? 'Hide' : 'Debug'}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-900/50 border border-red-700 rounded-lg">
          <p className="text-red-300 text-sm">{error}</p>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 md:gap-4 mb-6">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="text-xs text-gray-500 mb-1">Wallet Balance</div>
          <div className="text-xl font-bold text-green-400">
            {formatCurrency(balance)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="text-xs text-gray-500 mb-1">Positions Value</div>
          <div className="text-xl font-bold text-white">
            {formatCurrency(totalValue)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="text-xs text-gray-500 mb-1">Total P&L</div>
          <div className={`text-xl font-bold ${totalPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {formatPnL(totalPnL)}
          </div>
        </div>
        <div className="bg-gray-900 border border-yellow-700 rounded-lg p-4">
          <div className="text-xs text-yellow-500 mb-1">To Redeem</div>
          <div className="text-xl font-bold text-yellow-400">
            {formatCurrency(redeemableValue)}
          </div>
          <div className="text-xs text-yellow-600">{redeemablePositions.length} position{redeemablePositions.length !== 1 ? 's' : ''}</div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="text-xs text-gray-500 mb-1">Pending</div>
          <div className="text-xl font-bold text-blue-400">
            {pendingPositions.length}
          </div>
        </div>
      </div>

      {/* Redeemable Positions */}
      {redeemablePositions.length > 0 && (
        <div className="bg-gray-900 border border-yellow-700 rounded-lg p-4 mb-6">
          <h2 className="text-lg font-semibold mb-2 text-yellow-400 flex items-center gap-2">
            <span>🎉 Ready to Redeem</span>
            <span className="text-sm font-normal text-yellow-600">({formatCurrency(redeemableValue)} total)</span>
          </h2>
          <p className="text-xs text-yellow-600 mb-4">These positions have won and can be redeemed for USDC</p>
          <div className="space-y-2">
            {redeemablePositions.map((pos, i) => {
              const side = getPositionSide(pos)
              const shares = getPositionShares(pos)
              const pnl = pos.pnl || 0
              const value = pos.current_value || 0
              const cost = pos.cost_basis || 0
              
              return (
                <div key={i} className="p-3 rounded-lg bg-yellow-900/20 border border-yellow-700/50">
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate text-yellow-100">
                        {formatShortMarket(pos.question || 'Unknown')}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 mt-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          side === 'YES' 
                            ? 'bg-green-900/50 text-green-400' 
                            : 'bg-red-900/50 text-red-400'
                        }`}>
                          {side}
                        </span>
                        <span className="px-2 py-0.5 rounded text-xs font-medium border bg-yellow-900/50 text-yellow-400 border-yellow-700">
                          WON
                        </span>
                        <span className="text-xs text-yellow-500">
                          {shares.toFixed(1)} shares
                        </span>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-bold text-yellow-400">{formatCurrency(value)}</div>
                      <div className="text-xs font-medium text-green-400">
                        {formatPnL(pnl)} profit
                      </div>
                      <div className="text-xs text-gray-500">
                        cost: {formatCurrency(cost)}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Pending Positions */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <span>⏳ Pending Positions</span>
          <span className="text-sm font-normal text-gray-500">({pendingPositions.length})</span>
        </h2>
        {pendingPositions.length === 0 ? (
          <p className="text-gray-500 text-sm">No pending positions</p>
        ) : (
          <div className="space-y-2">
            {pendingPositions.map((pos, i) => {
              const side = getPositionSide(pos)
              const shares = getPositionShares(pos)
              const value = pos.current_value || 0
              const cost = pos.cost_basis || 0
              const avgPrice = pos.avg_cost || 0
              const resolveTime = pos.resolves_at ? new Date(pos.resolves_at) : null
              
              return (
                <div key={i} className="p-3 rounded-lg bg-blue-900/20 border border-blue-800/50">
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">
                        {formatShortMarket(pos.question || 'Unknown')}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 mt-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          side === 'YES' 
                            ? 'bg-green-900/50 text-green-400' 
                            : 'bg-red-900/50 text-red-400'
                        }`}>
                          {side}
                        </span>
                        <span className="px-2 py-0.5 rounded text-xs font-medium border bg-blue-900/50 text-blue-400 border-blue-700">
                          PENDING
                        </span>
                        <span className="text-xs text-gray-400">
                          {shares.toFixed(1)} @ {(avgPrice * 100).toFixed(0)}¢
                        </span>
                        {resolveTime && (
                          <span className="text-xs text-blue-400">
                            resolves {resolveTime.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-medium">{formatCurrency(value)}</div>
                      <div className="text-xs text-gray-500">
                        cost: {formatCurrency(cost)}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Resolved Positions (Won/Lost/Redeemed) */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <span>📊 Resolved Positions</span>
          <span className="text-sm font-normal text-gray-500">({resolvedPositions.length})</span>
        </h2>
        {resolvedPositions.length === 0 ? (
          <p className="text-gray-500 text-sm">No resolved positions yet</p>
        ) : (
          <div className="space-y-2">
            {resolvedPositions.map((pos, i) => {
              const status = getPositionStatus(pos)
              const side = getPositionSide(pos)
              const shares = getPositionShares(pos)
              const pnl = pos.pnl || 0
              const value = pos.current_value || 0
              const cost = pos.cost_basis || 0
              
              return (
                <div key={i} className={`p-3 rounded-lg border ${
                  status === 'WON' 
                    ? 'bg-green-900/10 border-green-800/50' 
                    : 'bg-red-900/10 border-red-800/50'
                }`}>
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate text-gray-400">
                        {formatShortMarket(pos.question || 'Unknown')}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 mt-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          side === 'YES' 
                            ? 'bg-green-900/50 text-green-400' 
                            : 'bg-red-900/50 text-red-400'
                        }`}>
                          {side}
                        </span>
                        <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getStatusBadge(status)}`}>
                          {status}
                        </span>
                        <span className="text-xs text-gray-500">
                          {shares.toFixed(1)} shares
                        </span>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-medium text-gray-400">{formatCurrency(value)}</div>
                      <div className={`text-xs font-medium ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {formatPnL(pnl)}
                      </div>
                      <div className="text-xs text-gray-500">
                        cost: {formatCurrency(cost)}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Recent Trades */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
        <h2 className="text-lg font-semibold mb-4">Recent Trades ({fastTrades.length})</h2>
        {fastTrades.length === 0 ? (
          <p className="text-gray-500 text-sm">No trades</p>
        ) : (
          <div className="space-y-2">
            {fastTrades.slice(0, 15).map((trade, i) => {
              const side = trade.side || '?'
              const action = trade.action || 'buy'
              const shares = trade.shares || 0
              const cost = trade.cost || 0
              const price = trade.price_before || (shares > 0 ? cost / shares : 0)
              
              return (
                <div key={i} className="p-3 rounded-lg bg-gray-800/50 border border-gray-700">
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate">
                        {formatShortMarket(trade.market_question || 'Unknown')}
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          action === 'sell' ? 'bg-yellow-900/50 text-yellow-400' :
                          side === 'yes' 
                            ? 'bg-green-900/50 text-green-400' 
                            : 'bg-red-900/50 text-red-400'
                        }`}>
                          {action === 'sell' ? 'SELL' : side.toUpperCase()}
                        </span>
                        <span className="text-xs text-gray-500">
                          {formatTradeTime(trade.created_at)}
                        </span>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-medium">{formatCurrency(cost)}</div>
                      <div className="text-xs text-gray-400">
                        {shares.toFixed(1)} @ {(price * 100).toFixed(0)}¢
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Debug Panel */}
      {showDebug && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
          <h2 className="text-lg font-semibold mb-4">Debug Info</h2>
          
          <div className="mb-4">
            <h3 className="text-sm font-medium text-gray-400 mb-2">API Calls</h3>
            <div className="bg-black/50 rounded p-3 max-h-32 overflow-y-auto font-mono text-xs">
              {apiLogs.map((log, i) => (
                <div key={i} className={log.status === 'OK' ? 'text-green-400' : 'text-red-400'}>
                  [{log.timestamp}] {log.endpoint}: {log.status}
                </div>
              ))}
            </div>
          </div>

          <div>
            <h3 className="text-sm font-medium text-gray-400 mb-2">Raw API Response</h3>
            <pre className="bg-black/50 rounded p-3 max-h-60 overflow-auto font-mono text-xs text-gray-300">
              {JSON.stringify(rawData, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="text-center text-xs text-gray-600">
        <p>Auto-refresh every 60s (API rate limited)</p>
      </div>
    </main>
  )
}
