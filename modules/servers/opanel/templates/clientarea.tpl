<div class="panel panel-default">
    <div class="panel-heading">
        <h3 class="panel-title">{$serviceLabel|escape}</h3>
    </div>
    <div class="panel-body">
        <table class="table table-condensed" style="margin-bottom:15px;">
            {if $status}
                <tr><td style="width:40%;"><strong>Status</strong></td><td>{$status|escape}</td></tr>
            {/if}
            {if $packageName}
                <tr><td><strong>Package</strong></td><td>{$packageName|escape}</td></tr>
            {/if}
            <tr><td><strong>Username</strong></td><td>{$username|escape}</td></tr>
            <tr><td><strong>Primary domain</strong></td><td>{if $domain}{$domain|escape}{else}Not attached{/if}</td></tr>
            {if $diskUsed}
                <tr>
                    <td><strong>Disk usage</strong></td>
                    <td>
                        {$diskUsed|escape}{if $diskLimit} / {$diskLimit|escape}{/if}
                        {if $diskPercent >= 0}
                            <div class="progress" style="margin:6px 0 0;height:8px;">
                                <div class="progress-bar{if $diskPercent >= 90} progress-bar-danger{elseif $diskPercent >= 75} progress-bar-warning{/if}" role="progressbar" style="width:{$diskPercent}%;"></div>
                            </div>
                        {/if}
                    </td>
                </tr>
            {/if}
            {if $websites}
                <tr><td><strong>Websites</strong></td><td>{$websites|escape}</td></tr>
            {/if}
            {if $createdAt}
                <tr><td><strong>Created</strong></td><td>{$createdAt|escape}</td></tr>
            {/if}
        </table>
        <a class="btn btn-primary" href="{$loginUrl|escape}" target="_blank" rel="noopener">
            Login to OPanel
        </a>
    </div>
</div>
