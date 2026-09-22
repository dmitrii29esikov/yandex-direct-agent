$ProgressPreference='SilentlyContinue'
$js = (Invoke-WebRequest -Uri 'https://printyard.spb.ru/assets/index-D5YijxGG.js' -UseBasicParsing -Headers @{'User-Agent'='Mozilla/5.0'}).Content

'--- ctx supabaseUrl ---'
$m = [regex]::Matches($js, 'supabaseUrl')
for ($i=0; $i -lt [Math]::Min($m.Count, 3); $i++) {
  $s = [Math]::Max(0, $m[$i].Index - 150); $l = [Math]::Min($js.Length - $s, 300)
  '...' + $js.Substring($s, $l) + '...'
  ''
}

'--- ctx insert( ---'
$m = [regex]::Matches($js, 'insert\(')
for ($i=0; $i -lt [Math]::Min($m.Count, 3); $i++) {
  $s = [Math]::Max(0, $m[$i].Index - 300); $l = [Math]::Min($js.Length - $s, 600)
  '...' + $js.Substring($s, $l) + '...'
  ''
}

'--- ctx requests / messages ---'
foreach ($p in 'requests','messages') {
  $mm = [regex]::Matches($js, $p, 'IgnoreCase')
  for ($i=0; $i -lt [Math]::Min($mm.Count, 3); $i++) {
    $s = [Math]::Max(0, $mm[$i].Index - 150); $l = [Math]::Min($js.Length - $s, 300)
    "[$p] ..." + $js.Substring($s, $l) + '...'
  }
  ''
}

'--- from( with quotes any ---'
[regex]::Matches($js, '\.from\(["' + [char]39 + '][A-Za-z_0-9]+["' + [char]39 + ']\)') | ForEach-Object { $_.Value } | Select-Object -Unique
