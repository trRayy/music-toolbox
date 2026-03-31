$env:FEISHU_APP_ID = "cli_a941fff3f1b9dcb3"
$env:FEISHU_APP_SECRET = "HgH1sFn1x27XjA0BvYebJpkGYyP5gwUH"
$env:FEISHU_REDIRECT_URI = "http://127.0.0.1:8000/callback"
python -u feishu_oauth_user_token.py *> oauth_run.log
