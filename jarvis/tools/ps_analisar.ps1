# Analisa um comando PowerShell (lido do stdin) com o parser oficial e devolve JSON.
# NÃO executa nada: só lista comandos, chamadas .NET, atribuições e redirecionamentos.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$codigo = [Console]::In.ReadToEnd()

$tokens = $null; $erros = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($codigo, [ref]$tokens, [ref]$erros)

$comandos = foreach ($c in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)) {
    $nome = $c.GetCommandName()
    $tipo = 'dinamico'; $resolvido = $null
    if ($nome) {
        $info = Get-Command -Name $nome -ErrorAction SilentlyContinue | Select-Object -First 1
        # alias -> comando real (ResolvedCommandName pode vir vazio com -NoProfile; Definition não)
        for ($i = 0; $info -and $info.CommandType -eq 'Alias' -and $i -lt 5; $i++) {
            $info = Get-Command -Name $info.Definition -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        if ($null -eq $info) { $tipo = 'desconhecido'; $resolvido = $nome }
        elseif ($info.CommandType -eq 'Application') { $tipo = 'externo'; $resolvido = [IO.Path]::GetFileNameWithoutExtension($info.Name) }
        else { $tipo = [string]$info.CommandType; $resolvido = $info.Name }
    }
    $params = @($c.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] } | ForEach-Object { $_.ParameterName })
    $valores = @($c.CommandElements | Select-Object -Skip 1 | Where-Object { $_ -isnot [System.Management.Automation.Language.CommandParameterAst] } | ForEach-Object { $_.Extent.Text })
    [pscustomobject]@{
        nome = $nome; resolvido = $resolvido; tipo = $tipo
        invocacao = [string]$c.InvocationOperator
        params = $params; valores = $valores
    }
}

$metodos = foreach ($m in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
    [pscustomobject]@{
        estatico = $m.Static
        alvo = $m.Expression.Extent.Text
        metodo = $m.Member.Extent.Text
    }
}

$atribuicoes_membro = @($ast.FindAll({
    $args[0] -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $args[0].Left -is [System.Management.Automation.Language.MemberExpressionAst] }, $true) | ForEach-Object { $_.Extent.Text })

$redirecionamentos = @($ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FileRedirectionAst] }, $true) | ForEach-Object { $_.Location.Extent.Text })

[pscustomobject]@{
    erros_sintaxe = @($erros | ForEach-Object { $_.Message })
    comandos = @($comandos)
    metodos = @($metodos)
    atribuicoes_membro = $atribuicoes_membro
    redirecionamentos = $redirecionamentos
} | ConvertTo-Json -Depth 5 -Compress
