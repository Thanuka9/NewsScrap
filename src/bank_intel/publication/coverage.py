"""Publisher coverage requirements shared by compilation and release checks."""
REQUIRED_SURFACES={
    'Daily FT': {'financial_services','front_page','top_story','homepage'},
    'Daily Mirror': {'business','homepage','breaking news','top stories','latest news'},
    'Daily News': {'business','local','homepage'},
    'The Morning': {'business','homepage'},
    'The Island': {'business','news','homepage'},
    'NewsFirst': {'latestpaginated','local','world','business','homepage'},
    'Ceylon Today': {'daily_finance','sunday_finance','homepage'},
}


# Collector defects a reviewer may accept with a written reason once the
# affected URLs are recorded. Anything else (collector failure, stale or
# missing status, missing sections, unrecorded failure URLs) leaves coverage
# unknown and can never be accepted.
ACCEPTABLE_ISSUES=('INVALID_ARTICLE_EXTRACTION','COLLECTOR_HEALTH:PARTIAL_ERROR','UNRESOLVED_FETCH_OR_DISCOVERY',
                   'INCOMPLETE_PAGINATION:','TRUNCATED_LISTING','MISSING_PUBLICATION_DATE')


def open_issues(evidence):
    """Automatic issues not covered by a signed, permitted acceptance."""
    accepted=evidence.get('accepted_issues') or {}
    return [i for i in evidence.get('automatic_issues',[])
            if not (i.startswith(ACCEPTABLE_ISSUES) and str(accepted.get(i) or '').strip())]


def missing_surfaces(publisher,surfaces):
    names={str(x.get('name') or x.get('listing_name') or '').casefold() if isinstance(x,dict) else str(x).casefold() for x in surfaces}
    return sorted(REQUIRED_SURFACES[publisher]-names)


def status_defects(publisher,info,report_date):
    defects=[]
    missing=missing_surfaces(publisher,info.get('discovery_surfaces') or [])
    if missing:defects.append({'kind':'MISSING_PUBLISHER_SECTIONS','sections':missing})
    if info.get('execution_status')!='SUCCESS':defects.append({'kind':'COLLECTOR_FAILED'})
    if info.get('health_status') not in ('SUCCESS','HEALTHY'):defects.append({'kind':'COLLECTOR_NOT_HEALTHY','status':info.get('health_status')})
    if str(info.get('completed_at',''))[:10]!=report_date:defects.append({'kind':'STALE_STATUS'})
    for field in ('invalid_extraction','missing_date','fetch_failures','truncated_discovery_urls','failed_discovery_surfaces','unresolved_fetch_urls'):
        if info.get(field):defects.append({'kind':field.upper(),'value':info[field]})
    for page in info.get('listing_pages',[]):
        if page.get('pagination_complete') is False:
            defects.append({'kind':'INCOMPLETE_PAGINATION','surface':page.get('listing_name'),'evidence':page})
    if info.get('wp_api_discovery',{}).get('api_pagination_complete') is False:
        defects.append({'kind':'INCOMPLETE_WORDPRESS_PAGINATION'})
    return defects
