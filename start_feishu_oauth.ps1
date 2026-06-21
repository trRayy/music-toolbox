param(
    [string]$AppId = $env:FEISHU_APP_ID,
    [string]$AppSecret = $env:FEISHU_APP_SECRET,
    [string]$RedirectUri = $(if ($env:FEISHU_REDIRECT_URI) { $env:FEISHU_REDIRECT_URI } else { "http://127.0.0.1:8000/callback" })
)

if ([string]::IsNullOrWhiteSpace($AppId) -or [string]::IsNullOrWhiteSpace($AppSecret)) {
    throw "Set FEISHU_APP_ID and FEISHU_APP_SECRET in your environment or pass -AppId/-AppSecret."
}

$env:FEISHU_APP_ID = $AppId
$env:FEISHU_APP_SECRET = $AppSecret
$env:FEISHU_REDIRECT_URI = $RedirectUri

python -u feishu_oauth_user_token.py
