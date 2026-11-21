// Standalone test of the private command-result parser. Do not link this TU
// with surface_core: including the implementation preserves the public API.
#include "../src/control_center.cpp"
#include <cassert>
#include <iostream>

int main() {
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
    assert(surface::control_detail::installProgress("{\"phase\":\"copy-image\",\"percent\":42}\n{\"phase\":").find("Step 2 of 4: Installing 403 OS - 42%")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"verify-target\",\"percent\":75}").find("Step 3 of 4")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"resize-unmounted-data-filesystem\"}").find("Step 4 of 4")!=std::string::npos);
    assert(surface::control_detail::installProgress("{\"phase\":\"copy-image\",\"percent\":999}").find("100%")!=std::string::npos);
    std::cout<<"Command JSON parsing, reviewed installer buttons, literal confirmation, changed-device refusal, and progress tests pass\n";
}
