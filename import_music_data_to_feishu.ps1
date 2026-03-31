param(
    [Parameter(Mandatory = $true)]
    [string]$Table,

    [string]$StartCell = "A1",

    [string]$SheetUrl = "https://gx1mlm3tj1l.feishu.cn/sheets/GdpOsM9orhCph3tbWFicv1YJn1g",

    [string]$WorksheetId = "db7efd",

    [string]$HostName = "localhost",

    [int]$Port = 3306,

    [string]$UserName = "root",

    [string]$Password = "root",

    [string]$Database = "t_music_data",

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
