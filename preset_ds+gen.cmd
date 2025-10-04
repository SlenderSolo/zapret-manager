set "BIN=%~dp0bin\"
set "LISTS=%~dp0lists\"
cd /d %BIN%

start "zapret: http,https,quic" /min "%BIN%winws.exe" --wf-tcp=80,443 --wf-udp=443,50000-50099 ^
--filter-tcp=80 --hostlist="%LISTS%list-general.txt" --dpi-desync=fakedsplit --dpi-desync-fooling=badseq --dpi-desync-split-pos=midsld --new ^
--filter-tcp=443 --hostlist="%LISTS%list-general.txt" --dpi-desync=fake,fakedsplit --dpi-desync-fooling=badseq --dpi-desync-split-pos=midsld --dpi-desync-fake-tls=0x00000000 --new ^
--filter-tcp=2053,2083,2087,2096,8443 --hostlist-domains=discord.media --dpi-desync=fake,fakedsplit --dpi-desync-fooling=badseq --dpi-desync-split-pos=midsld --dpi-desync-fake-tls=0x00000000 --new ^
--filter-tcp=443 --ipset="%LISTS%ipset-all.txt" --dpi-desync=fake,fakedsplit --dpi-desync-fooling=badseq --dpi-desync-split-pos=midsld --dpi-desync-fake-tls=0x00000000 --new ^
--filter-udp=443 --ipset="%LISTS%ipset-all.txt" --dpi-desync=fake --dpi-desync-repeats=11 --new ^
--filter-udp=443 --hostlist="%LISTS%list-general.txt" --dpi-desync=fake --dpi-desync-repeats=11 --new ^
--filter-udp=50000-50099 --filter-l7=discord,stun --dpi-desync=fake --dpi-desync-repeats=2
