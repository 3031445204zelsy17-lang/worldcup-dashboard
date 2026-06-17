// 48 队 martj42 全称 → ISO2(flagcdn.com). 含 subnational: England=gb-eng, Scotland=gb-sct
// flagcdn 支持这些代码(https://flagcdn.com/h40/gb-eng.webp)
export const TEAM_TO_ISO = {
  Spain: 'es', Argentina: 'ar', France: 'fr', England: 'gb-eng',
  Colombia: 'co', Brazil: 'br', Portugal: 'pt', Germany: 'de',
  Netherlands: 'nl', Morocco: 'ma', Japan: 'jp', Mexico: 'mx',
  Uruguay: 'uy', Belgium: 'be', Ecuador: 'ec', Croatia: 'hr',
  Norway: 'no', Australia: 'au', Switzerland: 'ch', Turkey: 'tr',
  Senegal: 'sn', 'South Korea': 'kr', 'United States': 'us', Iran: 'ir',
  Austria: 'at', Algeria: 'dz', Canada: 'ca', Paraguay: 'py',
  'Ivory Coast': 'ci', Scotland: 'gb-sct', Panama: 'pa', Uzbekistan: 'uz',
  Sweden: 'se', Egypt: 'eg', 'Czech Republic': 'cz', Jordan: 'jo',
  'DR Congo': 'cd', Iraq: 'iq', 'New Zealand': 'nz', 'Saudi Arabia': 'sa',
  Tunisia: 'tn', 'Bosnia and Herzegovina': 'ba', Haiti: 'ht', 'South Africa': 'za',
  'Cape Verde': 'cv', Ghana: 'gh', Qatar: 'qa', Curaçao: 'cw',
}

export function flagUrl(team, h = 40) {
  const iso = TEAM_TO_ISO[team]
  return iso ? `https://flagcdn.com/h${h}/${iso}.webp` : null
}
