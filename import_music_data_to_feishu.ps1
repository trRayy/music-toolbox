param(
    [Parameter(Mandatory = $true)]
    [string]$Table,

    [string]$StartCell = "A1",

    [string]$SheetUrl = "https://example.feishu.cn/sheets/YOUR_SPREADSHEET_TOKEN",

    [string]$WorksheetId = "YOUR_WORKSHEET_ID",

    [string]$HostName = "localhost",

    [int]$Port = 3306,

    [string]$UserName = "music_user",

    [string]$Password = "",

    [string]$Database = "music_db",

    [int]$FetchSize = 500
)

python .\mysql_table_to_feishu_sheet.py `
    --sheet-url $SheetUrl `
    --worksheet-id $WorksheetId `
    --table $Table `
    --start-cell $StartCell `
    --host $HostName `
    --port $Port `
    --user $UserName `
    --password $Password `
    --db $Database `
    --fetch-size $FetchSize
