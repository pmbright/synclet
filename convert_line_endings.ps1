# PowerShell script to convert files to Unix line endings

$files = @(
    "src\*.py",
    "scripts\*.sh",
    "scripts\*.sql",
    "config\*.yaml",
    "*.txt",
    "*.md",
    "setup.py"
)

$converted = 0

foreach ($pattern in $files) {
    $items = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue
    foreach ($file in $items) {
        if ($file.PSIsContainer) { continue }
        
        $content = Get-Content $file.FullName -Raw
        $content = $content -replace "`r`n", "`n"
        
        # Use UTF8 encoding without BOM for Unix compatibility
        [System.IO.File]::WriteAllText($file.FullName, $content, [System.Text.UTF8Encoding]::new($false))
        
        Write-Host "Converted: $($file.FullName)"
        $converted++
    }
}

Write-Host "`nTotal files converted: $converted"
