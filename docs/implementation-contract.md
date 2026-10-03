# Treasure Up 实现协作合同

2026-10-03。用户已授权按计划实现。正式代码在 backend/、frontend/、deploy/；实验保留，凭据、媒体和实验截图不提交。

## 共享规则

Python 3.12，FastAPI同步SQLAlchemy2 Session，生产PostgreSQL，测试SQLite内存库。UUID/平台ID均字符串。时间datetime UTC，JSON字段整值替换。主代理负责 app/models.py、db.py、config.py、security.py、API、队列与Docker。模块 `from app.models import ...`，`from app.db import SessionLocal`。

`app.config.settings`: database_url、redis_url、data_dir、scratch_dir、media_root、secret_key、cookie_secure、admin_username、admin_password、ffmpeg_path、ffprobe_path、yt_dlp_path、source_request_interval。路径为Path。

`app.security.encrypt_secret(text)` / `decrypt_secret(text)` 使用Fernet，缺密钥不得保存账号或云凭据。

## 共享 ORM 模型（主代理实现）

所有模型含id UUID字符串、created_at/updated_at datetime，Setting除外。

- User(username,password_hash,role)，UserSession(user_id,token_hash,csrf_token,expires_at)
- PlatformUser(uid,display_name,signature,avatar_asset_id,raw)，UserSnapshot(user_id,display_name,signature,avatar_asset_id,observed_at)，Creator(user_id,alias,description_override,notes,tags)
- Video(bvid,aid,title,description,published_at,duration,cover_asset_id,source_state,capture_status,metadata_json)，VideoAnnotation(video_id,title_override,description_override,notes,tags,starred)，VideoCreator(video_id,creator_id,role,role_title)，VideoPart(video_id,cid,position,title,duration)
- Collection(source_id,title,kind,owner_uid,enabled,last_scan_at)，CollectionItem(collection_id,source_resource_id,video_id,position,source_state,seen_run_id)
- Asset(sha256,size,mime_type,kind)，AssetLocation(asset_id,storage_profile_id,object_key,state,checksum,version_id,verified_at)，AssetRef(asset_id,entity_type,entity_id,purpose)，MediaVariant(part_id,asset_id,kind,format_key,quality,width,height,video_codec,audio_codec,duration)
- DanmakuSnapshot(part_id,run_id,status,expected_segments,completed_segments,raw_asset_ids,data_asset_id,count)，SubtitleTrack(part_id,language,label,is_auto,raw_asset_id,asset_id)
- Comment(video_id,rpid,root_rpid,parent_rpid,author_user_id,author_snapshot_id,content,posted_at,like_count,reply_count,raw)，CommentVersion(comment_id,run_id,content,like_count,author_snapshot_id,raw)，CommentAsset(comment_id,asset_id,kind,position)
- CaptureRun(video_id,collection_id,status,scope,checkpoint,counts,end_reason,started_at,finished_at)，SourceAccount(name,secret_encrypted,uid,status,last_verified_at)，SourceSubscription(collection_id,account_id,enabled,interval_minutes,policy,next_run_at)
- Job(kind,target_id,account_id,status,policy,checkpoint,result,error,attempts,max_attempts,dedupe_key,lease_owner,lease_expires_at,available_at,started_at,finished_at)，JobAttempt(job_id,status,error,started_at,finished_at)，OutboxEvent(job_id,published_at)
- StorageProfile(name,kind,config,secret_encrypted,is_default,enabled)，Setting(key,value)，AuditLog(actor_id,action,entity_type,entity_id,details)，WatchProgress(user_id,part_id,position,duration)，BackupSet(status,manifest,object_key,error,snapshot_at,completed_at)

## 模块合同

存储提供 `app.storage.service.ingest_file(db,path:Path,*,kind:str,mime_type:str,profile_id:str|None=None)->Asset`，验证后登记，不自行下载源文件。`resolve_asset(db,asset_id)`具体合同由存储代理追加。

采集提供 `app.ingest.runner.run_job(db,job:Job)` 支持 scan_collection/archive_video/refresh_comments/verify_account；主代理认领队列，采集保存检查点。网络凭据不得进入快照/日志/返回结果。媒体下载前稳定去重，分页不能伪装完成。

## API 与前端

base `/api/v1`，同源Cookie会话，写请求 `X-CSRF-Token`。GET `/auth/me` 返回 `{user:{id,username,role},csrf_token}`，401未登录；POST `/auth/login` body `{username,password}` 同响应，POST `/auth/logout`。错误 `{detail:string}`。列表统一 `{items,total,page,page_size}`，page从1，page_size<=100。

- GET `/library/stats` -> `{videos,creators,collections,assets_bytes,jobs_pending}`。
- GET `/videos?q=&creator_id=&collection_id=&tag=&starred=&page=&page_size=&sort=` -> 摘要 `{id,bvid,title,source_title,description,duration,cover_url,creators:[{id,name,avatar_url,role}],tags,starred,parts_count,playable,capture_status,created_at}`。
- GET `/videos/{id}` -> 摘要加 `notes,source_state,parts:[{id,cid,position,title,duration,variants:[{id,quality,kind,width,height,video_codec,audio_codec}]}],capture_runs:[]`。
- PATCH `/admin/videos/{id}` -> `{title_override,description_override,notes,tags,starred}`，null撤销覆盖。
- GET `/creators?q=&page=` -> `{id,uid,name,source_name,avatar_url,description,saved_count,tags}`；GET `/{id}` 加notes。PATCH `/admin/creators/{id}` -> `{alias,description_override,notes,tags}`。
- GET `/collections` -> `{id,title,kind,source_id,saved_count,enabled}`。
- GET `/videos/{id}/comments?root=&q=&page=` -> `{id,rpid,root_rpid,parent_rpid,content,author:{uid,name,avatar_url,creator_id},posted_at,like_count,reply_count,images:[]}`；root空仅顶层。
- POST `/playback-sessions` body `{part_id,variant_id?}` -> `{asset_id,variant_id,url,expires_at,danmaku_url,subtitles:[]}`。GET `/parts/{id}/danmaku` -> `[{text,time,color,mode,size}]`。
- PUT `/progress/{part_id}` body `{position,duration}`；GET同路径返回同字段。
- GET `/admin/overview` -> `{stats:{...},jobs:[],storage:[],backups:[]}`。
- GET/POST `/admin/accounts` 列表/输入 `{name,cookie}`，不返回凭据；POST `/{id}/verify`创建任务。
- GET/POST `/admin/sources` 列表/输入 `{source_id,title,account_id,enabled,interval_minutes,policy}`；POST `/{id}/scan`。
- GET `/admin/jobs?status=` 列表，POST创建 `{kind,target_id,account_id?,policy?}`；POST `/{id}/{retry|cancel|pause|resume}`。
- GET/POST `/admin/storage` 列表/输入 `{name,kind,config,credentials?,is_default,enabled}`；PATCH `/{id}`；POST `/{id}/probe`；POST `/{id}/migrate` body `{target_profile_id,asset_ids?:[]}`。
- GET/PATCH `/admin/settings` JSON对象；GET `/admin/backups`列表；POST创建备份任务。
- GET `/admin/audit`分页审计。写请求管理员权限与CSRF。

前端只显示真实数据和空状态。登录不展示默认密码。不能做无行为或假成功按钮。后台未支持功能明确禁用并解释。
