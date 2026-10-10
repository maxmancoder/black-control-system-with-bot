# -*- coding: utf-8 -*-
<#
    راه‌اندازی اجرای خودکار بات با Task Scheduler ویندوز.

    چرا Task Scheduler و نه سرویس ویندوز؟
    چون سرویس‌ها در Session 0 اجرا می‌شوند و به دسکتاپ دسترسی ندارند؛
    اسکرین‌شات و کلیک روی پنجره‌ها بدون دسترسی به دسکتاپ کار نمی‌کند.
    حالت "At log on" یعنی در نشست واقعی کاربر اجرا می‌شود.

    اجرا:
        powershell -ExecutionPolicy Bypass -File .\setup_task.ps1

    توجه: راهِ اجرای دستی هم در فایل start.bat ثبت شده است. هر تغییر در نحوه‌ی
    اجرا باید در CHANGE LOG داخل start.bat هم اضافه شود.
#>

param(
    [string]$TaskName = "BlackControlTelegramBot",
    [string]$BotDir = $PSScriptRoot,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$BotDir = (Resolve-Path -LiteralPath $BotDir).Path
$Script = Join-Path $BotDir "main.py"

if (-not (Test-Path -LiteralPath $Script)) {
    throw "main.py پیدا نشد: $Script"
}

# همان محیط مجازی‌ای که start.bat استفاده می‌کند
$VenvPython = Join-Path $BotDir ".venv\Scripts\python.exe"
if ([string]::IsNullOrWhiteSpace($Python)) {
    if (Test-Path -LiteralPath $VenvPython) {
        $Python = $VenvPython
    }
    else {
        $Python = "python"
    }
}

Write-Host "==> حذف نسخه‌ی قبلی تسک (اگر وجود دارد)"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Write-Host "==> ساخت اکشن اجرای پایتون"
$action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Script`"" `
    -WorkingDirectory $BotDir

Write-Host "==> اجرا در زمان ورود به سیستم، داخل نشست کاربر"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# اجرا فقط وقتی کاربر تعاملی (RPC) دارد، نه برای حالت Service
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Highest

Write-Host "==> تنظیمات اجرا"
# اگر کرش کرد، تا ۵ بار دوباره امتحان شود
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# تاخیر کوچک بعد از ورود، تا دسکتاپ آماده شود
$trigger.Delay = "PT30S"

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "ربات تلگرام کنترل لپ‌تاپ - فقط برای مالک" | Out-Null

Write-Host ""
Write-Host "تسک '$TaskName' ساخته شد." -ForegroundColor Green
Write-Host ""
Write-Host "اجرای دستی برای تست:"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "توقف:"
Write-Host "  Stop-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "حذف:"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ""
Write-Host "لاگ بات در این مسیر است:"
Write-Host "  $(Join-Path $BotDir 'logs\bot.log')"