param([Parameter(Mandatory=$true)][string]$InputPath, [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
$resolvedInput=(Resolve-Path -LiteralPath $InputPath).Path
$resolvedOutput=[IO.Path]::GetFullPath($OutputPath)
$wordApp=New-Object -ComObject Word.Application
$wordApp.Visible=$false
$wordApp.DisplayAlerts=0
$wordApp.AutomationSecurity=3
try {
    $openedDoc=$wordApp.Documents.Open($resolvedInput,$false,$true,$false)
    try { $openedDoc.ExportAsFixedFormat($resolvedOutput,17) }
    finally { $openedDoc.Close(0) }
    Write-Output ('Word ' + $wordApp.Version)
} finally {
    $wordApp.Quit()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApp)
}
