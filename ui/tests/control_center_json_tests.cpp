// Standalone test of the private command-result parser. Do not link this TU
// with surface_core: including the implementation preserves the public API.
#include "../src/control_center.cpp"
#include <cassert>
#include <iostream>

int main() {
    using surface::control_detail::BluetoothDevice;
    using surface::control_detail::bluetoothDevices;
    using surface::control_detail::bluetoothInfoName;
    const std::string btAddress="00:11:22:33:44:66";
    std::vector<BluetoothDevice> bluetooth;
    assert(bluetoothDevices(bluetooth,"[NEW] Device "+btAddress+" 00-11-22-33-44-66\n"));
    assert(bluetooth.size()==1 && bluetooth[0].name.empty());
    assert(bluetooth[0].label()=="Unnamed Bluetooth device (33:44:66)");
    assert(!bluetoothDevices(bluetooth,"[CHG] Device "+btAddress+" RSSI: -50\n"));
    assert(bluetoothDevices(bluetooth,"\x1b[0;93m[CHG]\x1b[0m Device "+btAddress+" Name: ipega PG-9087\n"));
    assert(bluetooth[0].name=="ipega PG-9087");
    assert(!bluetoothDevices(bluetooth,"Device "+btAddress+" 00:11:22:33:44:66\n"));
    assert(bluetooth[0].name=="ipega PG-9087"); // A stale address alias must not erase a resolved name.
    assert(bluetoothDevices(bluetooth,"[CHG] Device "+btAddress+" Alias: Player controller\n"));
    assert(bluetooth[0].label()=="Player controller (33:44:66)");
    assert(bluetoothInfoName("Device "+btAddress+" (public)\n\tName: ipega PG-9087\n\tAlias: 00-11-22-33-44-66\n",btAddress)=="ipega PG-9087");
    assert(bluetoothInfoName("\tName: Original name\n\tAlias: Custom controller\n",btAddress)=="Custom controller");
    assert(bluetoothDevices(bluetooth,"Device aa:bb:cc:dd:ee:ff Pokémon controller\n"));
    assert(bluetooth[1].mac=="AA:BB:CC:DD:EE:FF" && bluetooth[1].name=="Pokémon controller");
    assert(!bluetoothDevices(bluetooth,"garbage Device 00:11:22:33:44:55 Invalid\n[CHG] Device 00:11:22:33:44:55 Connected: yes\nDevice invalid Unknown\n"));
    assert(bluetoothDevices(bluetooth,"[DEL] Device "+btAddress+" Player controller\n"));
    assert(bluetooth.size()==1 && bluetooth[0].name=="Pokémon controller");
    assert(surface::control_detail::bluetoothName("  <unknown>  ",btAddress).empty());
    assert(surface::control_detail::bluetoothName(" 00-11-22-33-44-66 ",btAddress).empty());
    assert(surface::control_detail::bluetoothName(std::string(249,'x'),btAddress).empty());
    assert(surface::control_detail::bluetoothName(" Game\tpad\x7f ",btAddress)=="Gamepad");
    std::vector<BluetoothDevice> streaming;
    std::string pending="[NEW] Device "+btAddress+" 00-11-22-33-44-66\n[CHG] Device "+btAddress+" Name: Partial";
    assert(surface::control_detail::bluetoothDiscovery(streaming,pending));
    assert(streaming.size()==1 && streaming[0].name.empty());
    assert(!surface::control_detail::bluetoothDiscovery(streaming,pending));
    pending+=" controller\n";
    assert(surface::control_detail::bluetoothDiscovery(streaming,pending));
    assert(streaming[0].name=="Partial controller" && pending.empty());
    assert(!surface::control_detail::bluetoothDiscovery(streaming,pending));
    const std::string next="[CHG] Device "+btAddress+" Alias: Real controller\n";
    for(std::size_t index=0;index<next.size();++index) {
        pending+=next[index];
        assert(surface::control_detail::bluetoothDiscovery(streaming,pending)==(index+1==next.size()));
        assert(streaming[0].name==(index+1==next.size()?"Real controller":"Partial controller"));
    }
    for(unsigned index=0;index<100;++index) {
        char device[64]; std::snprintf(device,sizeof(device),"Device AA:BB:CC:DD:00:%02X Controller\n",index);
        bluetoothDevices(bluetooth,device);
    }
    assert(bluetooth.size()==64);
    const std::string rootProfile=R"({"event":"profile-status","authenticated":true,"current":{"name":"root","uid":0,"gid":0,"admin":true,"home":"/data/home","state":"/data/home"}})";
    const std::string playerProfile=R"({"event":"profile-status","authenticated":true,"current":{"name":"player","uid":2000,"gid":2000,"admin":false,"home":"/data/profiles/player/home","state":"/data/profiles/player/state"}})";
    const auto rootIdentity=surface::control_detail::authenticatedProfile(rootProfile);
    assert(rootIdentity && rootIdentity->uid==0 && rootIdentity->admin);
    const auto playerIdentity=surface::control_detail::authenticatedProfile(playerProfile);
    assert(playerIdentity && playerIdentity->uid==2000 && !playerIdentity->admin);
    for(const auto& replacement:std::vector<std::pair<std::string,std::string>>{{"\"uid\":2000","\"uid\":0"},{"\"admin\":false","\"admin\":true"},{"/data/profiles/player/home","/root"},{"\"gid\":2000","\"gid\":0"},{"\"authenticated\":true","\"authenticated\":false"}}) {
        auto invalid=playerProfile;const auto at=invalid.find(replacement.first);assert(at!=std::string::npos);
        invalid.replace(at,replacement.first.size(),replacement.second);
        assert(!surface::control_detail::authenticatedProfile(invalid));
    }
    assert(surface::jsonString("{\"value\":"+surface::jsonQuote("literal \\\"; $(no-shell)")+"}","value")=="literal \\\"; $(no-shell)");
    using surface::jsonString;
    assert(jsonString(R"({"path":"/media/Pok\u00e9mon/\ud3ec\ucf13\ubaac/\u96ea.nds"})","path")=="/media/Pokémon/포켓몬/雪.nds");
    assert(jsonString(R"({"path":"/media/\ud83c\udfae.exe"})","path")=="/media/🎮.exe");
    assert(jsonString(R"({"path":"\uDBFF\uDFFF"})","path")=="\xf4\x8f\xbf\xbf");
    assert(jsonString(R"({"path":"/media/Été/ゲーム.exe"})","path")=="/media/Été/ゲーム.exe");
    assert(jsonString(R"({"path":"C:\\Games\\A\"B\/app.exe"})","path")=="C:\\Games\\A\"B/app.exe");
    assert(jsonString(R"({"value":"a\nb\tc\rd\be\ff"})","value")=="a\nb\tc\rd\be\ff");
    assert(jsonString(R"({"value":"\u0000"})","value")==std::string(1,'\0'));
    for(const auto* malformed:{R"({"path":"\ud800"})",R"({"path":"\udc00"})",R"({"path":"\ud800\u0041"})",R"({"path":"\u12"})",R"({"path":"\u12xz"})",R"({"path":"\q"})",R"({"path":"unfinished)",R"({"path":null,"title":"other"})"})
        assert(jsonString(malformed,"path").empty());
    assert(jsonString("{\"path\":\"raw\nnewline\"}","path").empty());
    const auto objects=surface::jsonObjects(R"({"apps":[{"path":"/media/\u97f3\u697d/\ud83c\udfae.exe","title":"Pok\u00e9mon","runner":"wine"}]})","apps");
    assert(objects.size()==1);
    assert(jsonString(objects.front(),"path")=="/media/音楽/🎮.exe");
    assert(jsonString(objects.front(),"title")=="Pokémon");
    const auto paths=surface::jsonStrings(R"({"mount_paths":["/media/\u96ea","/media/\ud83c\udfae"]})","mount_paths");
    assert((paths==std::vector<std::string>{"/media/雪","/media/🎮"}));

    using surface::control_detail::InstallReview;
    const surface::control_detail::InstallSource source{"/data/SurfaceGoGamingOS/owner $(literal); surface-go.img",std::string(64,'a'),6510628864ULL};
    const surface::control_detail::InstallTarget target{"/dev/mmcblk0","mmc-SYNTHETIC 64;$(literal)","Synthetic eMMC",64ULL*1024*1024*1024};
    const auto plan=[&](const std::string& id,const std::string& path,const std::string& hash,std::uint64_t original,std::uint64_t grown) {
        return "{\"event\":\"plan\",\"confirmation\":\"ERASE "+id+"\",\"target\":\""+path+"\",\"source_sha256\":\""+hash+"\",\"data_original_bytes\":"+std::to_string(original)+",\"data_proposed_bytes\":"+std::to_string(grown)+"}";
    };
    const auto validPlan=plan(target.id,target.path,source.hash,4ULL*1024*1024*1024,60ULL*1024*1024*1024);
    const auto review=InstallReview::fromPlan(validPlan,source,target);
    assert(review);
    assert(review->rows().size()==7);
    assert(review->rows()[InstallReview::Cancel]=="Cancel");
    assert(review->rows()[InstallReview::Install]=="Install 403 OS - erase everything on this disk");
    assert(review->rows()[0].find("Synthetic eMMC (/dev/mmcblk0)")!=std::string::npos);
    assert(review->rows()[1].find("64.0 GiB")!=std::string::npos);
    assert(review->rows()[1].find("60.0 GiB")!=std::string::npos);
    for(std::size_t row=0;row<review->rows().size();++row)
        if(row!=InstallReview::Install) assert(review->executeArguments(row).empty());
    const auto execute=review->executeArguments(InstallReview::Install);
    assert((execute==std::vector<std::string>{"surface-install-device","--execute","--target",target.path,
        "--source",source.path,"--expected-target-id",target.id,"--expected-source-sha256",source.hash,"--confirm","ERASE "+target.id}));
    // Replaced devices, replaced media and malformed capacity never yield a
    // clickable destructive review. Shell metacharacters remain literal argv.
    for(const auto& invalid:{std::string{},plan("changed-device",target.path,source.hash,4ULL*1024*1024*1024,60ULL*1024*1024*1024),
        plan(target.id,"/dev/changed",source.hash,4ULL*1024*1024*1024,60ULL*1024*1024*1024),
        plan(target.id,target.path,std::string(64,'b'),4ULL*1024*1024*1024,60ULL*1024*1024*1024),
        plan(target.id,target.path,source.hash,8ULL*1024*1024*1024,60ULL*1024*1024*1024),
        plan(target.id,target.path,source.hash,4ULL*1024*1024*1024,4ULL*1024*1024*1024),
        plan(target.id,target.path,source.hash,4ULL*1024*1024*1024,target.bytes)})
        assert(!InstallReview::fromPlan(invalid,source,target));
    auto unidentified=target; unidentified.id.clear();
    assert(!InstallReview::fromPlan(validPlan,source,unidentified));
    auto invalidHash=source; invalidHash.hash=std::string(64,'z');
    assert(!InstallReview::fromPlan(validPlan,invalidHash,target));
    assert(surface::control_detail::installProgress("{\"phase\":\"copy-image\",\"percent\":42}\n{\"phase\":").find("Step 2 of 6: Installing 403 OS - 42%")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"preserve-user-data\",\"percent\":37}").find("Copying your games, saves and profiles - 37%")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"verify-user-data\",\"percent\":100}").find("Verifying your games and profiles - 100%")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"verify-target\",\"percent\":75}").find("Step 3 of 6")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"resize-unmounted-data-filesystem\"}").find("Step 4 of 6")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"copy-image\",\"percent\":999}").find("100%")!=std::string::npos);

    using surface::control_detail::UpdateReview;
    const auto updatePlan=[&](const std::string& id,const std::string& path,const std::string& hash,
                              const std::string& token,bool preserve,std::uint64_t bytes) {
        return "{\"event\":\"plan\",\"operation\":\"update\",\"confirmation\":\"UPDATE "+id+"\",\"target\":\""+path+"\",\"target_id\":\""+id+
            "\",\"target_bytes\":"+std::to_string(bytes)+",\"source\":"+surface::jsonQuote(source.path)+
            ",\"source_sha256\":\""+hash+"\",\"source_bytes\":"+std::to_string(source.bytes)+",\"review_token\":\""+token+"\",\"data_preserved\":"+(preserve?"true":"false")+
            ",\"automatic_restart\":false,\"backup_bytes\":2214592512,\"backup_required_bytes\":2751463424,"
            "\"partitions\":[{\"number\":1,\"name\":\"boot\",\"offset\":1048576,\"bytes\":67108864},{\"number\":2,\"name\":\"root\",\"offset\":68157440,\"bytes\":2147483648}]}";
    };
    const auto token=std::string(64,'c');
    const auto validUpdate=updatePlan(target.id,target.path,source.hash,token,true,target.bytes);
    const auto updateReview=UpdateReview::fromPlan(validUpdate,source,target);
    assert(updateReview && updateReview->rows().size()==7);
    assert(updateReview->rows()[UpdateReview::Cancel]=="Cancel");
    for(std::size_t row=0;row<updateReview->rows().size();++row)
        if(row!=UpdateReview::Update) assert(updateReview->executeArguments(row).empty());
    assert((updateReview->executeArguments(UpdateReview::Update)==std::vector<std::string>{
        "surface-update-device","--execute","--target",target.path,"--source",source.path,
        "--expected-target-id",target.id,"--expected-source-sha256",source.hash,
        "--review-token",token,"--confirm","UPDATE "+target.id}));
    // An update cannot reuse an erase review, switch devices or omit its
    // preservation/recovery plan. Both operations keep shell text literal.
    for(const auto& invalid:{validPlan,updatePlan("changed",target.path,source.hash,token,true,target.bytes),
        updatePlan(target.id,"/dev/changed",source.hash,token,true,target.bytes),
        updatePlan(target.id,target.path,std::string(64,'b'),token,true,target.bytes),
        updatePlan(target.id,target.path,source.hash,"",true,target.bytes),
        updatePlan(target.id,target.path,source.hash,token,false,target.bytes),
        updatePlan(target.id,target.path,source.hash,token,true,target.bytes-512)})
        assert(!UpdateReview::fromPlan(invalid,source,target));
    for(const auto& replacement:std::vector<std::pair<std::string,std::string>>{
        {"\"operation\":\"update\"","\"operation\":\"restore\""},
        {"\"source_bytes\":6510628864","\"source_bytes\":6510629376"},
        {"\"automatic_restart\":false","\"automatic_restart\":true"},
        {"\"backup_required_bytes\":2751463424","\"backup_required_bytes\":0"},
        {"\"backup_bytes\":2214592512","\"backup_bytes\":0"},
        {"\"number\":2,\"name\":\"root\"","\"number\":3,\"name\":\"data\""},
        {"\"offset\":68157440","\"offset\":2215636992"},
        {"\"bytes\":2147483648","\"bytes\":2147483647"}}) {
        auto invalid=validUpdate;const auto at=invalid.find(replacement.first);assert(at!=std::string::npos);
        invalid.replace(at,replacement.first.size(),replacement.second);
        assert(!UpdateReview::fromPlan(invalid,source,target));
    }
    const auto complete="{\"event\":\"complete\",\"operation\":\"update\",\"verified\":true,\"data_untouched\":true,"
        "\"gpt_unchanged\":true,\"target\":"+surface::jsonQuote(target.path)+",\"target_id\":"+surface::jsonQuote(target.id)+
        ",\"source_sha256\":"+surface::jsonQuote(source.hash)+",\"backup\":\"/data/updates/update-fixture\"}";
    assert(updateReview->completed("{\"event\":\"progress\",\"phase\":\"write-system\",\"percent\":100}\n"+complete));
    assert(!updateReview->completed("{\"event\":\"progress\",\"phase\":\"write-system\",\"percent\":100}"));
    assert(!updateReview->completed(complete+"\n{\"event\":\"error\",\"message\":\"verification failed\"}"));
    for(const auto& replacement:std::vector<std::pair<std::string,std::string>>{
        {"\"verified\":true","\"verified\":false"},
        {"\"data_untouched\":true","\"data_untouched\":false"},
        {"\"gpt_unchanged\":true","\"gpt_unchanged\":false"},
        {target.path,"/dev/changed"},{target.id,"changed"},{source.hash,std::string(64,'b')},
        {"/data/updates/update-fixture","relative-backup"}}) {
        auto invalid=complete;const auto at=invalid.find(replacement.first);assert(at!=std::string::npos);
        invalid.replace(at,replacement.first.size(),replacement.second);
        assert(!updateReview->completed(invalid));
    }
    assert(surface::control_detail::updateProgress("{\"phase\":\"backup-system\",\"percent\":37}").find("Backing up the current system - 37%")!=std::string::npos);
    assert(surface::control_detail::updateProgress("{\"phase\":\"verify-backup\",\"percent\":100}").find("Verifying the recovery copy - 100%")!=std::string::npos);
    assert(surface::control_detail::updateProgress("{\"phase\":\"write-system\",\"percent\":42}\n{\"phase\":").find("Updating 403 OS - 42%")!=std::string::npos);
    assert(surface::control_detail::updateProgress("{\"phase\":\"verify-system\",\"percent\":999}").find("Verifying the updated system - 100%")!=std::string::npos);
    std::cout<<"Command JSON parsing, reviewed installer buttons, literal confirmation, changed-device refusal, and progress tests pass\n";
}
