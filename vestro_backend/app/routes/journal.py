@router.get("/api/journal")
async def journal(account_id: str, limit: int = 50, db: AsyncSession = Depends(get_db)):
    token = await get_token_for_account(account_id, db)
    # pass token to your deriv journal fetcher
    return await fetch_journal(token, limit)

@router.get("/api/stats")
async def stats(account_id: str, days: int = 30, db: AsyncSession = Depends(get_db)):
    token = await get_token_for_account(account_id, db)
    return await fetch_stats(token, days)