set BIN=%~dp0bin\
set LST_YT=--hostlist="%~dp0lists\list-youtube.txt"
set LST_GEN=--hostlist="%~dp0lists\list-general.txt"
set IP_ALL=--ipset="%~dp0lists\ipset-all.txt"
set IP_EXC=--ipset-exclude="%~dp0lists\ipset-exclude.txt"
set LST_EXC=--hostlist-exclude="%~dp0lists\list-exclude.txt"
cd /d %BIN%

start "zapret: http,https,quic" /min "%BIN%winws.exe" --wf-tcp=80,443 ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.discord_media.txt" ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.stun.txt" ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.quic_initial_ietf.txt" ^
--filter-tcp=80 %LST_GEN% --dpi-desync=fake --dpi-desync-fooling=md5sig %IP_EXC% %LST_EXC% --new ^
--filter-tcp=443 %LST_YT% --ip-id=zero --dpi-desync=fake --dpi-desync-fooling=md5sig --dpi-desync-fake-tls="%BIN%tls_clienthello_www_google_com.bin" %IP_EXC% %LST_EXC% --new ^
--filter-tcp=443 %LST_GEN% --ip-id=zero --dpi-desync=fake --dpi-desync-fooling=md5sig --dpi-desync-fake-tls="%BIN%tls_clienthello_www_google_com.bin" %IP_EXC% %LST_EXC% --new ^
--filter-tcp=443 %IP_ALL% --ip-id=zero --dpi-desync=fake --dpi-desync-fooling=badseq --dpi-desync-fake-tls="%BIN%tls_clienthello_www_google_com.bin" %IP_EXC% %LST_EXC% --new ^
--filter-l7=quic %LST_YT% --dpi-desync=fake --dpi-desync-fake-quic="%BIN%quic_initial_www_google_com.bin" %IP_EXC% %LST_EXC% --new ^
--filter-l7=quic %LST_GEN% --dpi-desync=fake --dpi-desync-repeats=11 --dpi-desync-fake-quic="%BIN%quic_initial_www_google_com.bin" %IP_EXC% %LST_EXC% --new ^
--filter-l7=discord,stun --dpi-desync=fake --dpi-desync-fake-discord=0x00 --dpi-desync-fake-stun=0x00 --dpi-desync-repeats=6