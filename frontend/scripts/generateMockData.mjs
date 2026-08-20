import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

const companies = [
  { ticker: 'SH600588', name: '用友网络', industry: '企业软件' },
  { ticker: 'SZ000001', name: '平安银行', industry: '商业银行' },
  { ticker: 'SH600519', name: '贵州茅台', industry: '白酒' },
]

const years = [2021, 2022, 2023, 2024, 2025]

const reports = companies.flatMap((company, companyIndex) =>
  years.map((year, yearIndex) => ({
    id: `${company.ticker}-${year}`,
    type: 'annual-report',
    ticker: company.ticker,
    title: `${company.name}${year}年年报解析`,
    yaml_meta: {
      title: `${company.name}${year}年年报解析`,
      type: 'annual-report',
      ticker: company.ticker,
      report_year: year,
      tags: ['年报', company.industry, '价值投资'],
      credibility_score: 0.82,
      analysis_status: 'parsed',
      related: [`[[${company.name}]]`, `[[${company.name}${year}财务趋势]]`],
    },
    content_md: [
      `# ${company.name}${year}年年报解析`,
      '',
      '## 投资摘要',
      `${company.name}在${year}年保持核心业务韧性，收入和利润随行业周期波动。`,
      '',
      '## 财务表现与勾稽观察',
      `营收 ${120 + companyIndex * 60 + yearIndex * 12} 亿元，净利润 ${20 + companyIndex * 10 + yearIndex * 3} 亿元。`,
      '',
      '## 风险事件与治理问题',
      '需持续跟踪竞争格局、费用率、现金流和监管事件。',
    ].join('\n'),
  })),
)

const risks = Array.from({ length: 20 }).map((_, index) => {
  const company = companies[index % companies.length]
  return {
    id: `risk-${index + 1}`,
    ticker: company.ticker,
    type: 'risk-event',
    title: `${company.name}风险事件${index + 1}`,
    year: 2025 - (index % 5),
    category: ['诉讼', '监管问询', '担保', '内控缺陷'][index % 4],
    severity: ['low', 'medium', 'high'][index % 3],
    amount: `${(index + 1) * 850}万元`,
  }
})

const executives = Array.from({ length: 10 }).map((_, index) => {
  const company = companies[index % companies.length]
  return {
    id: `person-${index + 1}`,
    ticker: company.ticker,
    type: 'personnel-profile',
    name: `${company.name}高管${index + 1}`,
    role: ['董事长', '总经理', 'CFO', 'CTO', '独立董事'][index % 5],
    tenure: `${2018 + (index % 5)}-${index % 3 === 0 ? '至今' : 2024}`,
    note: 'Mock 履历：教育背景、过往企业、公开言论链接。',
  }
})

const news = Array.from({ length: 30 }).map((_, index) => {
  const company = companies[index % companies.length]
  return {
    id: `news-${index + 1}`,
    ticker: company.ticker,
    type: 'news-fragment',
    title: `${company.name}新闻片段${index + 1}`,
    source: ['公告', '媒体', '自媒体'][index % 3],
    weight: Number((0.95 - (index % 10) * 0.05).toFixed(2)),
    publish_date: `2025-${String((index % 12) + 1).padStart(2, '0')}-15`,
  }
})

const payload = { companies, reports, risks, executives, news }
const output = resolve('mock-data/valueverse-mock.json')
mkdirSync(dirname(output), { recursive: true })
writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
console.log(`Generated ${output}`)
console.log(`reports=${reports.length}, risks=${risks.length}, executives=${executives.length}, news=${news.length}`)
