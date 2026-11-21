// Standalone test of the private command-result parser. Do not link this TU
// with surface_core: including the implementation preserves the public API.
#include "../src/control_center.cpp"
#include <cassert>
#include <iostream>

int main() {
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
    std::cout<<"Command JSON Unicode, surrogate, escape, and malformed-input tests pass\n";
}
