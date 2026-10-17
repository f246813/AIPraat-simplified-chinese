    <div className="sidebar-header sidebar-top preview-compact-header">
      <div className="brand"><AudioLines size={16}/>{expanded&&<><strong>Praat</strong><SidebarOptions store={store} sort={sort} grouping={grouping} open={optionsOpen} onOpenChange={open=>{if(open)hover.hide();setOptionsOpen(open);}}/></>}</div>
      <div className="preview-header-actions">
        {expanded&&<IconButton label="搜索会话与消息" aria-expanded={previewSearchOpen} onClick={()=>{setPreviewSearchOpen(open=>!open);setQuery('');hover.hide();}}><Search size={15}/></IconButton>}
        <IconButton label={expanded?'折叠会话侧栏':'展开会话侧栏'} aria-expanded={expanded} onClick={onToggle}>{expanded?<PanelLeftClose size={15}/>:<PanelLeftOpen size={15}/>}</IconButton>
      </div>
    </div>
    <button className="new-session" title="新建会话" aria-label="新建会话" onClick={()=>create(null)}><SquarePen size={16}/>{expanded&&'新建会话'}</button>
    {expanded&&previewSearchOpen&&<label className="session-search"><Search size={15}/><input type="text" autoFocus aria-label="搜索会话与消息" placeholder="搜索会话与消息" spellCheck={false} autoCorrect="off" autoCapitalize="off" value={query} onKeyDown={event=>{if(event.key==='Escape'){setPreviewSearchOpen(false);setQuery('');}}} onChange={event=>{setQuery(event.target.value);hover.hide();setSelectedIds(new Set());setArmed(null);}}/>{query&&<IconButton label="清除搜索" onClick={()=>setQuery('')}><X size={13}/></IconButton>}</label>}
